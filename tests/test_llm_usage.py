from types import SimpleNamespace
import sys
import types

import pytest
from pydantic import BaseModel, SecretStr

import app.llm as llm
from app.llm import _rate_limit_details, _response_payload, _usage_counts


def test_gemini_usage_fields_are_normalized():
    usage = SimpleNamespace(prompt_token_count=135, candidates_token_count=29)
    assert _usage_counts("gemini", usage) == (135, 29)


def test_anthropic_usage_fields_are_normalized():
    usage = SimpleNamespace(input_tokens=135, output_tokens=29)
    assert _usage_counts("anthropic", usage) == (135, 29)


def test_groq_usage_fields_are_normalized():
    usage = {"prompt_tokens": 135, "completion_tokens": 29}
    assert _usage_counts("groq", usage) == (135, 29)


def test_groq_rate_limit_retry_after_is_read():
    request = llm.httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = llm.httpx.Response(429, headers={"retry-after": "2"},
                                  json={"error": {"message": "rate limit reached"}}, request=request)
    error = llm.httpx.HTTPStatusError("rate limited", request=request, response=response)
    assert _rate_limit_details(error) == (2.0, "rate limit reached")


def test_groq_chat_completion_payload_is_parsed():
    response = {"choices": [{"message": {"content": '{"queries": ["query"]}'}}]}
    assert _response_payload("groq", response) == {"queries": ["query"]}


@pytest.mark.asyncio
async def test_groq_ask_uses_chat_completion_and_logs_usage(monkeypatch):
    class Answer(BaseModel):
        result: str

    settings = SimpleNamespace(
        llm_provider="groq", groq_api_key=SecretStr("unit-test-key"),
        groq_api_base_url="https://api.groq.com/openai/v1/chat/completions",
        gemini_api_key=None, anthropic_api_key=None,
        llm_model_fast="openai/gpt-oss-20b", llm_model_strong="openai/gpt-oss-20b",
        groq_fast_input_usd_per_million=0.075, groq_fast_output_usd_per_million=0.30,
        groq_fallback_model="openai/gpt-oss-120b",
        groq_fallback_input_usd_per_million=0.15, groq_fallback_output_usd_per_million=0.60,
    )
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"result":"ok"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await llm.ask("Return the result", Answer, max_tokens=50)

    assert result.result == "ok"
    assert captured["json"]["model"] == "openai/gpt-oss-20b"
    assert captured["json"]["max_completion_tokens"] == 50
    assert captured["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_gemini_rate_limit_falls_back_to_groq(monkeypatch):
    class Answer(BaseModel):
        result: str

    settings = SimpleNamespace(
        llm_provider="gemini", gemini_api_key=SecretStr("gemini-test-key"),
        groq_api_key=SecretStr("groq-test-key"),
        groq_api_base_url="https://api.groq.com/openai/v1/chat/completions",
        llm_model_fast="gemini-3.1-flash-lite", llm_model_strong="gemini-3.6-flash",
        groq_fallback_model="openai/gpt-oss-120b",
        groq_fallback_input_usd_per_million=0.15,
        groq_fallback_output_usd_per_million=0.60,
    )

    class RateLimit(Exception):
        code = 429

    class Models:
        async def generate_content(self, **kwargs):
            raise RateLimit("quota exhausted")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"result":"fallback"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeHTTPClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            assert kwargs["json"]["model"] == "openai/gpt-oss-120b"
            return FakeResponse()

    gemini = types.SimpleNamespace(Client=lambda **kwargs: SimpleNamespace(
        aio=SimpleNamespace(models=Models())))
    monkeypatch.setitem(sys.modules, "google.genai", gemini)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: FakeHTTPClient())

    result = await llm.ask("Return the result", Answer, max_tokens=50)

    assert result.result == "fallback"


@pytest.mark.asyncio
async def test_groq_json_generation_failure_retries_with_smaller_model(monkeypatch):
    class Answer(BaseModel):
        result: str

    settings = SimpleNamespace(
        llm_provider="groq", groq_api_key=SecretStr("unit-test-key"),
        groq_api_base_url="https://api.groq.com/openai/v1/chat/completions",
        gemini_api_key=None, anthropic_api_key=None,
        llm_model_fast="openai/gpt-oss-20b", llm_model_strong="openai/gpt-oss-120b",
        groq_fallback_model="openai/gpt-oss-120b", groq_json_fallback_model="openai/gpt-oss-20b",
        groq_fallback_input_usd_per_million=0.15, groq_fallback_output_usd_per_million=0.60,
        groq_fast_input_usd_per_million=0.075, groq_fast_output_usd_per_million=0.30,
    )
    models = []

    class FailedResponse:
        def __init__(self, request):
            self.request = request

        def json(self):
            return {"error": {"message": "Failed to generate JSON. Adjust the prompt.",
                              "failed_generation": "not json"}}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"result":"recovered"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, **kwargs):
            model = kwargs["json"]["model"]
            models.append(model)
            if len(models) == 1:
                request = llm.httpx.Request("POST", url)
                response = FailedResponse(request)
                response.status_code = 400
                raise llm.httpx.HTTPStatusError("bad json", request=request, response=response)
            return FakeResponse()

    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await llm.ask("Return structured JSON", Answer, model=settings.groq_fallback_model,
                           max_tokens=50)

    assert result.result == "recovered"
    assert models == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_gemini_prefers_parsed_structured_response():
    parsed = {"queries": ["query"]}
    response = SimpleNamespace(parsed=parsed, text='{"queries": ["truncated')
    assert _response_payload("gemini", response) == parsed
