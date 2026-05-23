"""Integration tests for CorePipeline -- the unified execution engine."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a1.proxy.core_pipeline import CorePipeline, CorePipelineInput, CorePipelineResult
from a1.proxy.request_models import MessageInput
from a1.proxy.response_models import ChatCompletionResponse, Choice, ChoiceMessage, Usage


@pytest.fixture
def pipeline():
    return CorePipeline()


@pytest.fixture
def basic_input():
    return CorePipelineInput(
        request_id="test-req-001",
        source="openai",
        messages=[MessageInput(role="user", content="Hello, world")],
        raw_user_input="Hello, world",
        model="auto",
        max_tokens=100,
    )


@pytest.fixture
def atlas_input():
    return CorePipelineInput(
        request_id="test-req-002",
        source="atlas",
        messages=[MessageInput(role="user", content="Write a Python function")],
        raw_user_input="Write a Python function",
        model="atlas-code",
        max_tokens=500,
    )


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.name = "test-provider"
    provider.complete = AsyncMock(
        return_value=ChatCompletionResponse(
            id="test-resp",
            model="test-model",
            choices=[Choice(message=ChoiceMessage(content="Hello! I can help."))],
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
    )
    provider.estimate_cost = MagicMock(return_value=0.001)
    provider.list_models = MagicMock(return_value=[])
    return provider


class TestCorePipelineClassification:
    """Test the classify_and_resolve step."""

    @pytest.mark.asyncio
    async def test_atlas_model_bypasses_classifier(self, pipeline, atlas_input):
        task_type, confidence, atlas_model = await pipeline._classify_and_resolve(atlas_input)
        assert atlas_model == "atlas-code"
        assert confidence == 1.0
        assert task_type in ("coding", "code", "structured_extraction")

    @pytest.mark.asyncio
    async def test_auto_model_classifies(self, pipeline, basic_input):
        task_type, confidence, atlas_model = await pipeline._classify_and_resolve(basic_input)
        assert task_type is not None
        assert 0.0 <= confidence <= 1.0
        # auto model resolves to an atlas model (or None)
        assert atlas_model is None or atlas_model.startswith("atlas-")


class TestCorePipelineExecution:
    """Test full execute() flow with mocked providers."""

    @pytest.mark.asyncio
    @patch("a1.proxy.core_pipeline.settings")
    @patch("a1.proxy.core_pipeline.provider_registry")
    @patch("a1.proxy.core_pipeline.select_model")
    @patch("a1.proxy.core_pipeline._persist_usage")
    @patch("a1.proxy.core_pipeline.metrics")
    async def test_basic_non_streaming(
        self,
        mock_metrics,
        mock_persist,
        mock_select,
        mock_registry,
        mock_settings,
        pipeline,
        basic_input,
        mock_provider,
    ):
        mock_settings.session_enabled = False
        mock_settings.pii_masking_enabled = False
        mock_settings.distillation_enabled = False
        mock_settings.task_cache_enabled = False
        mock_settings.session_load_grace_ms = 100
        mock_settings.distillation_task_repeat_threshold = 0
        mock_settings.planning_max_depth = 3
        mock_settings.planning_max_workers = 5
        mock_settings.self_critique_enabled = False  # disable in unit tests
        mock_select.return_value = ("test-model", "test-provider")
        mock_registry.get_provider.return_value = mock_provider
        mock_registry.healthy_providers = {"test-provider": mock_provider}

        result = await pipeline.execute(basic_input)

        assert isinstance(result, CorePipelineResult)
        assert result.assistant_text == "Hello! I can help."
        assert result.error is None
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 20

    @pytest.mark.asyncio
    @patch("a1.proxy.core_pipeline.settings")
    async def test_error_returns_error_type(self, mock_settings, pipeline, basic_input):
        mock_settings.session_enabled = False
        mock_settings.pii_masking_enabled = False
        mock_settings.distillation_enabled = False
        mock_settings.task_cache_enabled = False
        mock_settings.session_load_grace_ms = 100
        mock_settings.distillation_task_repeat_threshold = 0
        mock_settings.planning_max_depth = 3
        mock_settings.planning_max_workers = 5
        mock_settings.self_critique_enabled = False  # disable in unit tests

        # No providers available
        with patch("a1.proxy.core_pipeline.provider_registry") as mock_reg:
            mock_reg.get_provider.return_value = None
            mock_reg.healthy_providers = {}
            with patch("a1.proxy.core_pipeline.select_model", return_value=("x", "y")):
                result = await pipeline.execute(basic_input)

        assert result.error is not None
        # No chat provider available for the model → model_not_found
        # (more specific than the old generic provider_error).
        assert result.error_type == "model_not_found"


class TestCorePipelineResult:
    """Test result dataclass defaults."""

    def test_default_values(self):
        r = CorePipelineResult()
        assert r.response_id == ""
        assert r.assistant_text is None
        assert r.error is None
        assert r.is_local is False
        assert r.cache_hit is False
        assert r.distillation is False

    def test_response_id_preserved(self):
        r = CorePipelineResult(response_id="test-123")
        assert r.response_id == "test-123"
