import httpx
import pytest
import respx

from app.ai_provider import (
    AIProvider,
    AIProviderError,
    ChatConfig,
    ImageConfig,
    chat_completions_endpoint,
    discover_models,
    models_endpoint,
)


def test_chat_endpoint_accepts_provider_base_url():
    assert chat_completions_endpoint("https://api.deepseek.com") == "https://api.deepseek.com/chat/completions"
    assert chat_completions_endpoint("https://api.example.com/v1") == "https://api.example.com/v1/chat/completions"


def test_chat_endpoint_preserves_full_endpoint():
    value = "https://api.example.com/v1/chat/completions"
    assert chat_completions_endpoint(value) == value


def test_models_endpoint_from_chat_completions():
    assert models_endpoint("https://api.example.com/v1/chat/completions") == "https://api.example.com/v1/models"


def test_models_endpoint_from_image_generation():
    assert models_endpoint("https://api.example.com/v1/images/generations") == "https://api.example.com/v1/models"


@respx.mock
def test_discover_models_parses_and_deduplicates_openai_response():
    route = respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}, {"id": "deepseek-chat"}]})
    )

    result = discover_models("https://api.example.com/v1/chat/completions", "secret")

    assert result == ["deepseek-chat", "deepseek-reasoner"]
    assert route.calls[0].request.headers["authorization"] == "Bearer secret"


@respx.mock
def test_discover_models_reports_empty_list():
    respx.get("https://api.example.com/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

    with pytest.raises(AIProviderError, match="没有返回"):
        discover_models("https://api.example.com/v1/chat/completions", "secret")


@respx.mock
def test_chat_posts_to_resolved_chat_completions_endpoint():
    route = respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "完成"}}], "usage": {"total_tokens": 5}},
        )
    )
    provider = AIProvider(
        ChatConfig(endpoint="https://api.deepseek.com", api_key="secret", model="deepseek-v4-flash"),
        ImageConfig(endpoint="", api_key="", model=""),
    )

    content, usage = provider.chat([{"role": "user", "content": "测试"}])

    assert content == "完成"
    assert usage["total_tokens"] == 5
    assert route.called
