import pytest
import httpx
import respx
from dataclasses import replace
from pathlib import Path
from starlette.testclient import TestClient

from app.ai_provider import AIProvider, AIProviderError, ChatConfig, ImageConfig
import app.main as main
from app.security import safe_request, validate_public_url


def test_security_rejects_private_literal_addresses():
    with pytest.raises(ValueError):
        validate_public_url("http://127.0.0.1/feed.xml")
    with pytest.raises(ValueError):
        validate_public_url("http://169.254.169.254/latest/meta-data")


def test_security_rejects_non_http_schemes():
    with pytest.raises(ValueError):
        validate_public_url("file:///etc/passwd")


@respx.mock
def test_safe_request_rejects_redirect_to_private_host_before_following():
    respx.get("https://public.example/feed.xml").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    )

    with httpx.Client(follow_redirects=False) as client:
        with pytest.raises(ValueError, match="本机|内网|元数据"):
            safe_request(client, "GET", "https://public.example/feed.xml")


@respx.mock
def test_safe_request_does_not_follow_post_redirects():
    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(307, headers={"location": "https://api.example.com/other"})
    )

    with httpx.Client(follow_redirects=False) as client:
        with pytest.raises(ValueError, match="重定向"):
            safe_request(client, "POST", "https://api.example.com/v1/chat/completions", json={})


@respx.mock
def test_image_api_rejects_content_type_and_signature_mismatch():
    respx.post("https://api.example.com/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/not-an-image"}]})
    )
    respx.get("https://cdn.example.com/not-an-image").mock(
        return_value=httpx.Response(200, content=b"not-an-image", headers={"content-type": "image/jpeg"})
    )
    provider = AIProvider(
        ChatConfig(endpoint="", api_key="", model=""),
        ImageConfig(
            endpoint="https://api.example.com/v1/images/generations",
            api_key="secret",
            model="image-model",
        ),
    )

    with pytest.raises(AIProviderError, match="格式"):
        provider.generate_image("technology illustration")


def test_media_mount_only_exposes_image_directory(monkeypatch, tmp_path: Path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "safe.jpg").write_bytes(b"image")
    monkeypatch.setattr(main, "settings", replace(main.settings, data_dir=tmp_path))

    client = TestClient(main.create_app())
    assert client.get("/media/images/safe.jpg").status_code == 200
    assert client.get("/media/app.db").status_code == 404
    assert client.get("/media/models/topic_recommender.joblib").status_code == 404
