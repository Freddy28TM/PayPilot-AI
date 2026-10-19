"""Offline tests for grounded Gemini explanations."""

from types import SimpleNamespace

from pydantic import SecretStr

from backend.config import Settings
from backend.explainer import ExplanationOutput, GeminiExplainer

EVIDENCE = {
    "id": "ORDER-TEST",
    "status": "COMPLETED",
    "payer": {"email_address": "private@example.test"},
    "internal_test_field": "must-not-reach-ai",
}

ASSESSMENT = {
    "classification": "completed_settlement_unverified",
    "observed_status": "COMPLETED",
    "settlement_verified": False,
    "summary": "The order is completed, but settlement is not verified.",
    "next_step": "Check appropriate transaction reporting evidence.",
}


class FakeModels:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class FakeGeminiClient:
    def __init__(self, response):
        self.models = FakeModels(response)


def test_missing_api_key_uses_deterministic_fallback():
    settings = Settings(_env_file=None, gemini_api_key=None)
    result = GeminiExplainer(settings).explain(EVIDENCE, ASSESSMENT)

    assert result == {
        "status": "fallback",
        "explanation": ASSESSMENT["summary"],
    }


def test_valid_response_generates_explanation_and_limits_input():
    client = FakeGeminiClient(
        SimpleNamespace(
            parsed=ExplanationOutput(
                explanation=(
                    "The order is complete, but this evidence does not "
                    "confirm settlement."
                )
            )
        )
    )
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-test-key"),
        gemini_model="synthetic-test-model",
    )

    result = GeminiExplainer(settings, client).explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "generated"
    assert "settlement" in result["explanation"]
    prompt = client.models.kwargs["contents"]
    assert "private@example.test" not in prompt
    assert "must-not-reach-ai" not in prompt
    assert "next_step" not in prompt
    assert (
        client.models.kwargs["config"].response_mime_type
        == "application/json"
    )


def test_invalid_structured_response_falls_back():
    client = FakeGeminiClient(
        SimpleNamespace(parsed={"unexpected_field": "unsupported"})
    )
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-test-key"),
    )

    result = GeminiExplainer(settings, client).explain(EVIDENCE, ASSESSMENT)

    assert result == {
        "status": "fallback",
        "explanation": ASSESSMENT["summary"],
    }


def test_provider_failure_falls_back_without_leaking_error():
    client = FakeGeminiClient(None)

    def fail(**kwargs):
        raise RuntimeError("synthetic provider failure and secret detail")

    client.models.generate_content = fail
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-test-key"),
    )

    result = GeminiExplainer(settings, client).explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "fallback"
    assert result["explanation"] == ASSESSMENT["summary"]
    assert "secret detail" not in str(result)


def test_empty_explanation_falls_back():
    client = FakeGeminiClient(
        SimpleNamespace(parsed=ExplanationOutput(explanation="   "))
    )
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-test-key"),
    )

    result = GeminiExplainer(settings, client).explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "fallback"
    assert result["explanation"] == ASSESSMENT["summary"]

def test_provider_timeout_falls_back_without_leaking_error():
    client = FakeGeminiClient(None)

    def timeout(**kwargs):
        raise TimeoutError("synthetic timeout with secret detail")

    client.models.generate_content = timeout
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-test-key"),
    )

    result = GeminiExplainer(settings, client).explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "fallback"
    assert result["explanation"] == ASSESSMENT["summary"]
    assert "secret detail" not in str(result)


class FakeOpenRouterResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {"message": {"content": self.content}}
            ]
        }


class FakeOpenRouterClient:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeOpenRouterResponse(self.content)


class FailingGeminiModels:
    def generate_content(self, **kwargs):
        raise TimeoutError("synthetic Gemini timeout")


class FailingGeminiClient:
    def __init__(self):
        self.models = FailingGeminiModels()


def test_openrouter_success_without_gemini():
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("synthetic-test-key"),
        openrouter_model="synthetic-test-model",
    )
    router = FakeOpenRouterClient(
        '{"explanation":"The order is awaiting further verification."}'
    )
    explainer = GeminiExplainer(settings, openrouter_client=router)

    result = explainer.explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "generated"
    assert result["provider"] == "openrouter"
    assert result["explanation"] == (
        "The order is awaiting further verification."
    )
    assert len(router.calls) == 1
    assert router.calls[0]["json"]["model"] == "synthetic-test-model"
    assert router.calls[0]["json"]["provider"] == {
        "require_parameters": True
    }
    assert "synthetic-test-key" not in str(result)


def test_gemini_failure_falls_back_to_openrouter():
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-gemini-key"),
        openrouter_api_key=SecretStr("synthetic-openrouter-key"),
        openrouter_model="synthetic-test-model",
    )
    router = FakeOpenRouterClient(
        '{"explanation":"The available order evidence needs review."}'
    )
    explainer = GeminiExplainer(
        settings,
        client=FailingGeminiClient(),
        openrouter_client=router,
    )

    result = explainer.explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "generated"
    assert result["provider"] == "openrouter"
    assert len(router.calls) == 1
    assert "synthetic-gemini-key" not in str(result)
    assert "synthetic-openrouter-key" not in str(result)


def test_invalid_openrouter_response_uses_deterministic_fallback():
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("synthetic-test-key"),
        openrouter_model="synthetic-test-model",
    )
    router = FakeOpenRouterClient("not valid JSON")
    explainer = GeminiExplainer(settings, openrouter_client=router)

    result = explainer.explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "fallback"
    assert result["explanation"] == ASSESSMENT["summary"]
    assert len(router.calls) == 1
    assert "synthetic-test-key" not in str(result)


def test_openrouter_is_primary_when_both_providers_are_configured():
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-gemini-key"),
        openrouter_api_key=SecretStr("synthetic-openrouter-key"),
        openrouter_model="synthetic-test-model",
    )
    router = FakeOpenRouterClient(
        '{"explanation":"The available order evidence needs review."}'
    )
    gemini = FakeGeminiClient(None)
    explainer = GeminiExplainer(
        settings, client=gemini, openrouter_client=router
    )

    result = explainer.explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "generated"
    assert result["provider"] == "openrouter"
    assert len(router.calls) == 1
    assert gemini.models.kwargs is None


def test_gemini_is_used_when_openrouter_fails():
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-gemini-key"),
        openrouter_api_key=SecretStr("synthetic-openrouter-key"),
        openrouter_model="synthetic-test-model",
    )
    router = FakeOpenRouterClient("not valid JSON")
    gemini = FakeGeminiClient(
        SimpleNamespace(
            parsed=ExplanationOutput(
                explanation="The order evidence needs further review."
            )
        )
    )
    explainer = GeminiExplainer(
        settings, client=gemini, openrouter_client=router
    )

    result = explainer.explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "generated"
    assert result["provider"] == "gemini"
    assert len(router.calls) == 1
    assert gemini.models.kwargs is not None


def test_explanation_does_not_mutate_deterministic_assessment():
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("synthetic-test-key"),
        openrouter_model="synthetic-test-model",
    )
    router = FakeOpenRouterClient(
        '{"explanation":"Settlement is verified."}'
    )
    explainer = GeminiExplainer(settings, openrouter_client=router)
    original_assessment = dict(ASSESSMENT)

    result = explainer.explain(EVIDENCE, ASSESSMENT)

    assert result["status"] == "generated"
    assert result["explanation"] == "Settlement is verified."
    assert ASSESSMENT == original_assessment
    assert ASSESSMENT["settlement_verified"] is False
    assert ASSESSMENT["classification"] == "completed_settlement_unverified"
