from pydantic import Field
from pydantic.aliases import AliasChoices
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "A1_", "env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # App
    app_name: str = "Alpheric.AI"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # Database (no default -- must be set via A1_DATABASE_URL env var or .env file)
    database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Provider API keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    vertex_project_id: str = ""
    vertex_location: str = "us-central1"
    # Vertex AI / Gemini extended config
    vertex_api_key: str = ""                           # A1_VERTEX_API_KEY (Google AI Studio key)
    vertex_auth_type: str = "api_key"                  # "api_key" | "service_account"
    vertex_default_model: str = "gemini-2.0-flash"     # A1_VERTEX_DEFAULT_MODEL
    vertex_web_search_enabled: bool = False            # A1_VERTEX_WEB_SEARCH_ENABLED
    vertex_timeout: float = 60.0                       # A1_VERTEX_TIMEOUT
    # Tenant sources (atlas_api_keys.source values) whose requests are forced
    # to Vertex/Gemini regardless of normal routing. Used to pin specific
    # external services to a known-cheap, known-fast model. e.g. notifire →
    # gemini-2.0-flash. Falls back to normal routing if Vertex is unhealthy.
    vertex_forced_sources: list[str] = ["notifire"]    # A1_VERTEX_FORCED_SOURCES
    # Model used when vertex_forced_sources matches. Kept distinct from
    # vertex_default_model (which is used for grounding/vision and may be
    # tuned for quality) so the forced-tenant pin can be set independently.
    # NB: started on gemini-2.0-flash, but that returns 404 ("no longer
    # available to new users") on Google AI Studio. Moved to 2.5-flash, then
    # to 2.5-pro after callers reported the flash model not honouring
    # response_format (JSON-mode) reliably enough — Pro follows structured-
    # output instructions more consistently.
    vertex_forced_sources_model: str = "gemini-2.5-pro"  # A1_VERTEX_FORCED_SOURCES_MODEL

    # Ollama (supports multiple servers)
    ollama_base_url: str = "http://localhost:11434"
    ollama_servers: list[str] = [
        "http://10.0.0.9:11434",   # Code models (deepseek-coder, llama3.2)
        "http://10.0.0.10:11434",  # QA/reasoning models (codellama, deepseek-r1, mistral)
    ]

    # OpenClaw gateway
    openclaw_url: str = ""
    openclaw_token: str = ""

    # Atlas model family
    atlas_models: list[str] = [
        "atlas-plan", "atlas-code", "atlas-secure",
        "atlas-infra", "atlas-data", "atlas-books", "atlas-audit",
        "atlas-image",
    ]

    # Proxy auth
    api_keys: list[str] = []

    # Routing
    exploration_rate: float = 0.1
    default_strategy: str = "best_quality"

    # Training
    training_min_samples: int = 500
    training_min_quality: float = 0.7
    training_base_model: str = "mistralai/Mistral-7B-Instruct-v0.3"
    training_lora_rank: int = 16
    training_output_dir: str = "./training_outputs"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Open-source integrations ---
    use_litellm: bool = True
    use_unsloth: bool = True

    # GPTCache
    cache_enabled: bool = False
    cache_similarity_threshold: float = 0.8
    cache_ttl_seconds: int = 3600
    cache_embedding: str = "local"
    cache_db_path: str = "./cache/gptcache.db"

    # OpenTelemetry
    otlp_endpoint: str = ""

    # Argilla
    argilla_api_url: str = ""
    argilla_api_key: str = ""
    argilla_workspace: str = "default"
    argilla_handoff_gate_enabled: bool = True  # require Argilla annotation approval before handoff increment
    argilla_approval_threshold: float = 0.8    # fraction of annotated records rated ≥4/5 required for approval

    # lm-evaluation-harness
    use_harness_eval: bool = False
    harness_default_tasks: list[str] = ["mmlu", "hellaswag", "truthfulqa_mc2"]
    harness_num_fewshot: int = 5
    harness_batch_size: int = 4

    # Multi-account key pool
    key_pool_strategy: str = "round_robin"
    encryption_key: str = ""

    # Claude CLI multi-account pool
    # List of Linux usernames whose ~/.claude/.credentials.json should be used.
    # Each user must already be logged in via `claude login`.
    # If empty, falls back to the current process user.
    claude_cli_users: list[str] = []

    # Distillation / Auto-training
    distillation_enabled: bool = True
    distillation_claude_model: str = "claude-opus-4-20250514"
    distillation_min_samples: int = 100
    distillation_quality_threshold: float = 0.7
    distillation_handoff_increment: float = 0.1
    distillation_max_handoff_pct: float = 0.9
    # After this many requests of the same task_type in the current server process,
    # route to local model directly (if healthy) without waiting for full training.
    # Set to 0 to disable. Default: 10 — enough to warm up Ollama without training.
    distillation_task_repeat_threshold: int = 10
    # Per-request timeout for the local "student" model during distillation.
    # Hit by qwen2.5-coder:7b on slower / loaded GPU servers — bumped from the
    # original 30s after observing ~44% of distillation runs timing out (and
    # producing similarity=0.00 samples that polluted the training set).
    distillation_local_timeout_seconds: int = 90
    # When True, distillation samples where the local student produced no
    # text (or scored exactly 0.0 against the teacher) are NOT persisted as
    # training data. Stops the dataset from filling with zeros when the
    # local model is too slow / broken.
    distillation_skip_zero_similarity: bool = True

    # Session memory
    session_enabled: bool = True
    session_ttl_seconds: int = 3600  # 1 hour
    session_max_messages: int = 20  # max history to include per request
    # Token budget for session history injection. If injected history would exceed
    # this many tokens, oldest messages are dropped to stay within limit.
    # 0 = no token budget (rely on session_max_messages count only).
    session_max_history_tokens: int = 40000

    # PII masking (enterprise)
    pii_masking_enabled: bool = True
    pii_mask_for_external_only: bool = True  # only mask for Claude, not Ollama
    pii_patterns: list[str] = ["email", "phone", "ssn", "credit_card", "api_key", "ip_address", "aws_key", "password"]

    # Groq
    groq_api_key: str = ""

    # Moonshot / Kimi
    moonshot_api_key: str = ""

    # Platform features
    computer_use_enabled: bool = False
    agent_execution_timeout: int = 6000   # seconds per agent turn (100 min)
    planning_max_depth: int = 3           # CEO→Manager→Worker hierarchy depth
    planning_max_workers: int = 5         # parallel agent workers per plan

    # Agent builder resilience
    # When the primary provider (claude-cli / Alpheric) times out or errors during an
    # agent_builder run, automatically retry on OpenAI gpt-4o if the key is configured.
    # Disabled by default so cost/behaviour changes are opt-in.
    agent_builder_openai_fallback: bool = False   # A1_AGENT_BUILDER_OPENAI_FALLBACK
    # Hard timeout for a single agent execution turn (seconds).
    # Cloudflare's origin timeout is 100s — keep this under that to avoid 520s.
    agent_builder_timeout_s: int = 55             # A1_AGENT_BUILDER_TIMEOUT_S

    # Phase 1: performance
    parallel_dual_execution: bool = True          # fire local model concurrently with external
    session_load_grace_ms: int = 100              # max ms to wait for session before proceeding
    task_cache_enabled: bool = True               # per-task-type in-memory response cache (P1-7)

    # Multi-model management
    warm_up_models: list[str] = []
    reference_external_model: str = "gpt-4o-mini"

    # Self-Heal Model
    self_critique_enabled: bool = True
    quality_min_score: float = 0.40           # below this → trigger self-critique
    quality_critique_model: str = "claude-haiku-4-5"  # fast, cheap critique model
    feedback_regen_enabled: bool = True       # thumbs-down triggers regeneration
    health_monitor_interval_seconds: int = 300  # how often to scan conversations (seconds)

    # Web Search
    web_search_enabled: bool = False          # master switch; opt-in
    web_search_intent_threshold: int = 50     # intent score 0-100 required to trigger search
    web_search_max_results: int = 5           # max results to fetch per query
    web_search_timeout_s: float = 10.0        # wall-clock timeout for the search API call
    web_search_depth: str = "basic"           # "basic" | "advanced" (Tavily)
    web_search_extract_pages: bool = True     # fetch + clean page content for top N results
    web_search_extract_max: int = 3           # how many pages to extract
    web_search_extract_timeout_s: float = 8.0  # timeout for page extraction batch

    # Web Search Provider API keys
    tavily_api_key: str = ""    # A1_TAVILY_API_KEY
    exa_api_key: str = ""       # A1_EXA_API_KEY
    brave_api_key: str = ""     # A1_BRAVE_API_KEY

    # ── Provisioning API (OneDesk / platform-to-platform) ─────────────────────
    # Secret key OneDesk sends in Authorization: Bearer <key> for provisioning calls.
    # Never used for chat/completion — provisioning only.
    # Accepts both ALPHERIC_AI_PLATFORM_API_KEY and A1_ALPHERIC_AI_PLATFORM_API_KEY.
    alpheric_ai_platform_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ALPHERIC_AI_PLATFORM_API_KEY",
            "A1_ALPHERIC_AI_PLATFORM_API_KEY",
        ),
    )
    alpheric_ai_base_url: str = Field(
        default="https://atlas.alpheric.ai/v1",
        validation_alias=AliasChoices("ALPHERIC_AI_BASE_URL", "A1_ALPHERIC_AI_BASE_URL"),
    )
    alpheric_ai_default_model: str = Field(
        default="Atlas",
        validation_alias=AliasChoices("ALPHERIC_AI_DEFAULT_MODEL", "A1_ALPHERIC_AI_DEFAULT_MODEL"),
    )
    atlas_api_key_prefix: str = Field(
        default="sk-atlas",
        validation_alias=AliasChoices("ATLAS_API_KEY_PREFIX", "A1_ATLAS_API_KEY_PREFIX"),
    )
    # Provisioning rate limit (requests per minute per IP)
    provision_rate_limit_rpm: int = 10              # A1_PROVISION_RATE_LIMIT_RPM

    # File uploads
    upload_dir: str = "/var/www/dev/atlas/uploads"  # A1_UPLOAD_DIR
    upload_max_bytes: int = 512 * 1024 * 1024        # A1_UPLOAD_MAX_BYTES (512 MB)

    # ── Cloudflare AI Gateway ──────────────────────────────────────────────────
    # When enabled, all outbound AI provider calls are routed through the CF AI
    # Gateway, which adds caching, rate limiting, cost tracking, and real-time
    # logs without changing the API contract.
    #
    # Setup (one-time):
    #   1. Cloudflare Dashboard → AI → AI Gateway → Create Gateway
    #   2. Copy the Account ID (from any CF dashboard URL or "Account Home")
    #   3. Note the gateway name you chose
    #   4. Set the three env vars below and restart Atlas
    #
    # Gateway URL format (auto-built by provider_base_url()):
    #   https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_name}/{provider}
    #
    # Per-provider overrides (openai_base_url etc.) take priority over the
    # auto-generated gateway URL.  Leave empty to use SDK defaults (bypass CF).
    cf_ai_gateway_enabled: bool = False          # A1_CF_AI_GATEWAY_ENABLED
    cf_ai_gateway_account_id: str = ""           # A1_CF_AI_GATEWAY_ACCOUNT_ID
    cf_ai_gateway_name: str = ""                 # A1_CF_AI_GATEWAY_NAME

    # Per-provider base URL overrides (empty = use CF gateway if enabled, else SDK default)
    # Use these to point a single provider at a custom proxy, regional endpoint,
    # or a different CF gateway than the default one above.
    openai_base_url: str = ""       # A1_OPENAI_BASE_URL   (replaces https://api.openai.com/v1)
    anthropic_base_url: str = ""    # A1_ANTHROPIC_BASE_URL (replaces https://api.anthropic.com)
    groq_base_url: str = ""         # A1_GROQ_BASE_URL      (replaces https://api.groq.com/openai/v1)

    def provider_base_url(self, provider: str) -> str | None:
        """Resolve the effective base URL for a provider.

        Priority order:
          1. Explicit per-provider override  (e.g. A1_OPENAI_BASE_URL)
          2. Cloudflare AI Gateway auto-URL  (when A1_CF_AI_GATEWAY_ENABLED=true)
          3. None  →  SDK / LiteLLM default

        CF AI Gateway URL template per provider:
          https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_name}/{provider_path}

        Provider paths recognised:
          openai → /openai       (replaces https://api.openai.com/v1)
          anthropic → /anthropic (replaces https://api.anthropic.com)
          groq → /groq           (replaces https://api.groq.com/openai/v1)
        """
        # 1. Explicit per-provider override
        explicit = getattr(self, f"{provider}_base_url", "")
        if explicit:
            return explicit

        # 2. CF AI Gateway
        cf_provider_paths: dict[str, str] = {
            "openai": "openai",
            "anthropic": "anthropic",
            "groq": "groq",
        }
        if (
            self.cf_ai_gateway_enabled
            and self.cf_ai_gateway_account_id
            and self.cf_ai_gateway_name
            and provider in cf_provider_paths
        ):
            path = cf_provider_paths[provider]
            return (
                f"https://gateway.ai.cloudflare.com/v1"
                f"/{self.cf_ai_gateway_account_id}"
                f"/{self.cf_ai_gateway_name}"
                f"/{path}"
            )

        # 3. SDK default
        return None


settings = Settings()
