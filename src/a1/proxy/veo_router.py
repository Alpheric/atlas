"""Veo video generation API endpoints.

POST /v1/video/generate   — text-to-video
POST /v1/video/animate    — image-to-video
GET  /v1/video/models     — list available Veo models
GET  /v1/video/status/{operation_id}  — check job status (future: async mode)

Auth: same API key auth as other Atlas endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from a1.common.auth import verify_api_key
from a1.common.logging import get_logger

log = get_logger("proxy.veo_router")

router = APIRouter(prefix="/v1/video", tags=["video"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class VideoGenerateRequest(BaseModel):
    prompt: str = Field(..., description="Text description of the video to generate")
    model: str = Field("veo-3.0-generate-preview", description="Veo model to use")
    aspect_ratio: str = Field("16:9", description='"16:9" | "9:16"')
    duration_seconds: int = Field(8, ge=5, le=8, description="Video length in seconds (5–8)")
    negative_prompt: str = Field("", description="What to avoid in the video")
    seed: int | None = Field(None, description="Random seed for reproducibility")
    enhance_prompt: bool = Field(True, description="Let Veo rewrite prompt for better quality")


class VideoAnimateRequest(BaseModel):
    prompt: str = Field(..., description="Animation prompt / motion description")
    image_url: str = Field(..., description="Base image: GCS URI (gs://...) or HTTPS URL")
    model: str = Field("veo-3.0-generate-preview", description="Veo model to use")
    aspect_ratio: str = Field("16:9", description='"16:9" | "9:16"')
    duration_seconds: int = Field(8, ge=5, le=8)


class VideoResponse(BaseModel):
    operation_id: str
    model: str
    video_uri: str = ""
    video_download_url: str = ""
    duration_seconds: float = 0.0
    aspect_ratio: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
    ok: bool = False
    error: str = ""


class VeoModelInfo(BaseModel):
    name: str
    description: str = ""
    max_duration_seconds: int = 8
    supported_aspect_ratios: list[str] = ["16:9", "9:16"]
    cost_per_second: float = 0.0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/models", response_model=list[VeoModelInfo])
async def list_video_models(_: str = Depends(verify_api_key)):
    """List available Veo video generation models."""
    from a1.providers.veo import veo_provider

    return [
        VeoModelInfo(
            name=m.get("name", ""),
            description=m.get("description", ""),
            max_duration_seconds=m.get("max_duration_seconds", 8),
            supported_aspect_ratios=m.get("supported_aspect_ratios", ["16:9", "9:16"]),
            cost_per_second=m.get("cost_per_second", 0.0),
        )
        for m in veo_provider.list_models()
    ]


@router.post("/generate", response_model=VideoResponse)
async def generate_video(
    req: VideoGenerateRequest,
    _: str = Depends(verify_api_key),
):
    """Generate a video from a text prompt using Veo.

    This is synchronous — the request will hold open until the video is ready
    (typically 30–120 seconds). For fire-and-forget use the async endpoint (coming soon).
    """
    from a1.providers.veo import VeoRequest, veo_provider

    log.info(
        f"[veo] POST /v1/video/generate model={req.model} "
        f"aspect={req.aspect_ratio} prompt={req.prompt[:60]!r}"
    )

    try:
        result = await veo_provider.generate(
            VeoRequest(
                prompt=req.prompt,
                model=req.model,
                aspect_ratio=req.aspect_ratio,
                duration_seconds=req.duration_seconds,
                negative_prompt=req.negative_prompt,
                seed=req.seed,
                enhance_prompt=req.enhance_prompt,
            )
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log.warning(f"[veo] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Video generation failed: {e}")

    return VideoResponse(
        operation_id=result.operation_id,
        model=result.model,
        video_uri=result.video_uri,
        video_download_url=result.video_download_url,
        duration_seconds=result.duration_seconds,
        aspect_ratio=result.aspect_ratio,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        ok=result.ok,
        error=result.error,
    )


@router.post("/animate", response_model=VideoResponse)
async def animate_image(
    req: VideoAnimateRequest,
    _: str = Depends(verify_api_key),
):
    """Animate a base image into video using Veo image-to-video."""
    from a1.providers.veo import veo_provider

    log.info(
        f"[veo] POST /v1/video/animate model={req.model} image={req.image_url[:60]!r}"
    )

    try:
        result = await veo_provider.image_to_video(
            prompt=req.prompt,
            image_url=req.image_url,
            model=req.model,
            aspect_ratio=req.aspect_ratio,
            duration_seconds=req.duration_seconds,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log.warning(f"[veo] Animate error: {e}")
        raise HTTPException(status_code=500, detail=f"Animation failed: {e}")

    return VideoResponse(
        operation_id=result.operation_id,
        model=result.model,
        video_uri=result.video_uri,
        duration_seconds=result.duration_seconds,
        aspect_ratio=result.aspect_ratio,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        ok=result.ok,
        error=result.error,
    )
