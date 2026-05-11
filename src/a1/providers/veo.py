"""Google Veo video generation provider.

Veo 3 / Veo 2 via Vertex AI — text-to-video and image-to-video generation.

Auth: uses the same Vertex AI service-account ADC or API key as VertexProvider.
  - Requires A1_VERTEX_PROJECT_ID=atlas-ai-model
  - Veo 3 requires Google Cloud approval (limited preview as of 2026-05)

API flow (async job):
  1. POST  .../models/{model}:predictLongRunning  → returns operation name
  2. GET   .../operations/{op_id}               → poll until done=true
  3. Extract video GCS URI from response

The provider exposes a synchronous-style interface that hides the polling loop.
Results are returned as VeoResult (not ChatCompletionResponse) since they carry
video URIs, not text.

Endpoints:
  https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}
    /publishers/google/models/{model}:predictLongRunning
  https://{location}-aiplatform.googleapis.com/v1/{operation_name}

Config: see config/providers.yaml veo section.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field

import httpx
import yaml

from a1.common.logging import get_logger
from a1.providers.base import ModelInfo
from config.settings import settings

log = get_logger("providers.veo")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class VeoRequest:
    """Input for a Veo video generation job."""

    prompt: str
    model: str = "veo-3.0-generate-preview"
    aspect_ratio: str = "16:9"  # "16:9" | "9:16"
    duration_seconds: int = 8  # 5–8 supported
    negative_prompt: str = ""
    seed: int | None = None
    image_url: str = ""  # optional: image-to-video base frame (GCS URI or https)
    enhance_prompt: bool = True  # let Veo rewrite prompt for better results


@dataclass
class VeoResult:
    """Output from a completed Veo generation job."""

    operation_id: str
    model: str
    video_uri: str = ""  # GCS URI: gs://bucket/path/video.mp4
    video_download_url: str = ""  # signed HTTPS URL (if generated)
    duration_seconds: float = 0.0
    aspect_ratio: str = "16:9"
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str = ""
    raw_metadata: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.video_uri) and not self.error


# ---------------------------------------------------------------------------
# Model config loader
# ---------------------------------------------------------------------------


def _load_veo_models() -> list[dict]:
    try:
        with open("config/providers.yaml") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("providers", {}).get("veo", {}).get("models", [])
    except Exception as e:
        log.warning(f"[veo] Could not load models from providers.yaml: {e}")
        return [
            {
                "name": "veo-3.0-generate-preview",
                "max_duration_seconds": 8,
                "cost_per_second": 0.0035,
            }
        ]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class VeoProvider:
    """Google Veo video generation provider.

    Uses Vertex AI's predictLongRunning endpoint.
    Requires: A1_VERTEX_PROJECT_ID, A1_VERTEX_LOCATION (or API key for preview endpoints).
    """

    name = "veo"
    _DEFAULT_POLL_INTERVAL = 5.0  # seconds between operation polls
    _MAX_POLL_SECONDS = 300  # 5-minute timeout for video generation

    def __init__(self):
        self.project_id = settings.vertex_project_id
        self.location = settings.vertex_location or "us-central1"
        self.api_key = settings.vertex_api_key
        self.auth_type = settings.vertex_auth_type
        self.timeout = max(settings.vertex_timeout, self._MAX_POLL_SECONDS + 10)
        self._models = _load_veo_models()
        self._client = httpx.AsyncClient(timeout=self.timeout)

        # SA token cache (shared logic with VertexProvider)
        self._sa_token: str | None = None
        self._sa_token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _get_sa_bearer(self) -> str | None:
        now = time.time()
        if self._sa_token and now < self._sa_token_expiry - 60:
            return self._sa_token
        try:
            import google.auth  # type: ignore[import]
            import google.auth.transport.requests  # type: ignore[import]

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            req = google.auth.transport.requests.Request()
            creds.refresh(req)
            self._sa_token = creds.token
            self._sa_token_expiry = creds.expiry.timestamp() if creds.expiry else now + 3600
            return self._sa_token
        except Exception as e:
            log.warning(f"[veo] ADC token fetch failed: {e}")
            return None

    async def _auth_headers(self) -> dict[str, str] | None:
        if self.auth_type == "api_key" and self.api_key:
            return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        if self.project_id:
            bearer = await self._get_sa_bearer()
            if bearer:
                return {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
        return None

    def _base_url(self) -> str:
        return (
            f"https://{self.location}-aiplatform.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{self.location}"
        )

    def _generate_url(self, model: str) -> str:
        return f"{self._base_url()}/publishers/google/models/{model}:predictLongRunning"

    def _operation_url(self, op_name: str) -> str:
        # op_name is like "projects/.../operations/xxx"
        return f"https://{self.location}-aiplatform.googleapis.com/v1/{op_name}"

    # ------------------------------------------------------------------
    # Request builder
    # ------------------------------------------------------------------

    def _build_payload(self, req: VeoRequest) -> dict:
        """Build the Vertex AI Veo request payload."""
        instance: dict = {
            "prompt": req.prompt,
        }
        if req.negative_prompt:
            instance["negativePrompt"] = req.negative_prompt
        if req.image_url:
            instance["image"] = (
                {"gcsUri": req.image_url}
                if req.image_url.startswith("gs://")
                else {"bytesBase64Encoded": req.image_url}
            )

        parameters: dict = {
            "aspectRatio": req.aspect_ratio,
            "durationSeconds": str(req.duration_seconds),
            "enhancePrompt": req.enhance_prompt,
        }
        if req.seed is not None:
            parameters["seed"] = req.seed

        return {
            "instances": [instance],
            "parameters": parameters,
        }

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    async def generate(self, req: VeoRequest) -> VeoResult:
        """Submit a video generation job and poll until complete.

        Returns VeoResult — check .ok and .video_uri.
        Raises RuntimeError on auth failure or API error.
        """
        if not self.project_id:
            raise RuntimeError(
                "[veo] A1_VERTEX_PROJECT_ID (atlas-ai-model) is required for Veo generation"
            )

        headers = await self._auth_headers()
        if headers is None:
            raise RuntimeError(
                "[veo] No valid auth — set A1_VERTEX_PROJECT_ID or A1_VERTEX_API_KEY"
            )

        t0 = time.time()
        model = req.model
        url = self._generate_url(model)
        payload = self._build_payload(req)
        _op_id = f"veo-{uuid.uuid4().hex[:12]}"

        log.info(
            f"[veo] Submitting generation: model={model} aspect={req.aspect_ratio} "
            f"duration={req.duration_seconds}s prompt={req.prompt[:60]!r}"
        )

        # Step 1: Submit job
        resp = await self._client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            body = resp.text
            log.warning(f"[veo] Submit failed {resp.status_code}: {body[:200]}")
            raise RuntimeError(f"Veo API error {resp.status_code}: {body[:200]}")

        op_data = resp.json()
        op_name = op_data.get("name", "")
        if not op_name:
            raise RuntimeError(f"[veo] No operation name in response: {op_data}")

        log.info(f"[veo] Job submitted: {op_name}")

        # Step 2: Poll until done
        result = await self._poll_operation(op_name, headers, t0)
        result.model = model
        result.aspect_ratio = req.aspect_ratio
        result.duration_seconds = req.duration_seconds

        # Estimate cost
        model_cfg = next((m for m in self._models if m["name"] == model), {})
        cost_per_sec = model_cfg.get("cost_per_second", 0.003)
        result.cost_usd = cost_per_sec * req.duration_seconds

        return result

    async def _poll_operation(
        self,
        op_name: str,
        headers: dict,
        t0: float,
    ) -> VeoResult:
        """Poll the LRO until done=true or timeout."""
        op_url = self._operation_url(op_name)
        deadline = t0 + self._MAX_POLL_SECONDS
        interval = self._DEFAULT_POLL_INTERVAL

        while time.time() < deadline:
            await asyncio.sleep(interval)
            # Refresh headers each poll (bearer token may expire on long jobs)
            current_headers = await self._auth_headers() or headers

            poll_resp = await self._client.get(op_url, headers=current_headers)
            if poll_resp.status_code != 200:
                log.warning(f"[veo] Poll error {poll_resp.status_code}: {poll_resp.text[:100]}")
                interval = min(interval * 1.5, 30)  # backoff
                continue

            op_data = poll_resp.json()
            done = op_data.get("done", False)
            latency_ms = int((time.time() - t0) * 1000)

            if done:
                return self._extract_result(op_data, op_name, latency_ms)

            # Increase poll interval slightly to reduce API calls on long jobs
            interval = min(interval + 2, 15)
            log.debug(f"[veo] Still running ({latency_ms}ms) — next poll in {interval:.0f}s")

        raise RuntimeError(f"[veo] Job timed out after {self._MAX_POLL_SECONDS}s: {op_name}")

    def _extract_result(self, op_data: dict, op_name: str, latency_ms: int) -> VeoResult:
        """Parse a completed LRO response into VeoResult."""
        error = op_data.get("error")
        if error:
            return VeoResult(
                operation_id=op_name,
                model="",
                error=f"{error.get('code', 'unknown')}: {error.get('message', '')}",
                latency_ms=latency_ms,
                raw_metadata=op_data,
            )

        resp = op_data.get("response", {})
        videos = resp.get("videos", [])
        if not videos:
            # Try predictions path (older format)
            videos = op_data.get("predictions", [{}])[0].get("videos", [])

        video_uri = ""
        if videos:
            first = videos[0]
            video_uri = first.get("gcsUri", "") or first.get("uri", "")

        log.info(f"[veo] Generation complete in {latency_ms}ms: {video_uri or 'no URI'}")

        return VeoResult(
            operation_id=op_name,
            model="",
            video_uri=video_uri,
            latency_ms=latency_ms,
            raw_metadata=op_data,
        )

    # ------------------------------------------------------------------
    # Convenience: text-to-video shorthand
    # ------------------------------------------------------------------

    async def text_to_video(
        self,
        prompt: str,
        model: str = "veo-3.0-generate-preview",
        aspect_ratio: str = "16:9",
        duration_seconds: int = 8,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> VeoResult:
        """High-level shorthand for text-to-video generation."""
        return await self.generate(
            VeoRequest(
                prompt=prompt,
                model=model,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
                negative_prompt=negative_prompt,
                seed=seed,
            )
        )

    async def image_to_video(
        self,
        prompt: str,
        image_url: str,
        model: str = "veo-3.0-generate-preview",
        aspect_ratio: str = "16:9",
        duration_seconds: int = 8,
    ) -> VeoResult:
        """Animate a base image into video."""
        return await self.generate(
            VeoRequest(
                prompt=prompt,
                image_url=image_url,
                model=model,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
            )
        )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check that Veo credentials are configured.

        Note: Veo requires Vertex AI (GCP) credentials — either a service account
        via ADC or a project_id with api_key. We verify config presence here;
        actual API errors surface on the first generation request.
        """
        # Must have at least one auth method
        if self.api_key or self.project_id:
            return True
        return False

    def list_models(self) -> list[ModelInfo]:
        """Return models as ModelInfo objects so registry.list_providers() works correctly."""
        return [
            ModelInfo(
                name=m["name"],
                provider="veo",
                context_window=0,
                cost_per_1k_input=0.0,
                cost_per_1k_output=round(m.get("cost_per_second", 0.0035) * 1000, 4),
                supports_tools=False,
                supports_streaming=False,
                tier="frontier",
                latency_class="batch",
            )
            for m in self._models
        ]

    def supports_model(self, model: str) -> bool:
        return any(m["name"] == model for m in self._models)


# Singleton
veo_provider = VeoProvider()
