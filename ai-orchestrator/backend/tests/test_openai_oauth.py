import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from openai_oauth import OpenAIOAuthStore  # noqa: E402


def test_authorization_url_contains_pkce_state_and_chatgpt_plan_scopes(tmp_path):
    store = OpenAIOAuthStore(tmp_path / "oauth.json")
    result = store.begin_authorization("https://ha.example/api/openai/oauth/callback")

    query = parse_qs(urlparse(result["authorization_url"]).query)
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["redirect_uri"] == ["https://ha.example/api/openai/oauth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert "chatgpt.tokens.use.direct" in query["scope"][0].split()
    assert result["state"]
    assert result["expires_at"] > 0


def test_callback_rejects_state_mismatch(tmp_path):
    store = OpenAIOAuthStore(tmp_path / "oauth.json")
    store.begin_authorization("https://ha.example/callback")

    with pytest.raises(ValueError, match="state"):
        asyncio.run(store.complete_callback("code", "wrong-state", "issued-client"))


def test_access_token_is_persisted_without_leaking_from_status(tmp_path, monkeypatch):
    store = OpenAIOAuthStore(tmp_path / "oauth.json")
    pending = store.begin_authorization("https://ha.example/callback")

    async def fake_exchange(*args, **kwargs):
        return {
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
            "expires_in": 3600,
            "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            "id_token": "header.payload.signature",
        }

    monkeypatch.setattr(store, "_exchange_code", fake_exchange)
    asyncio.run(store.complete_callback("code", pending["state"], "issued-client"))

    status = store.status()
    assert status["authenticated"] is True
    assert "access_token" not in status
    assert "refresh_token" not in status
    raw = json.loads((tmp_path / "oauth.json").read_text())
    assert raw["access_token"] == "secret-access"


def test_refresh_is_used_when_access_token_expired(tmp_path, monkeypatch):
    store = OpenAIOAuthStore(tmp_path / "oauth.json")
    store._write({
        "client_id": "issued-client",
        "refresh_token": "refresh",
        "access_token": "expired",
        "expires_at": 0,
        "scope": "chatgpt.tokens.use.direct",
    })

    async def fake_refresh(*args, **kwargs):
        return {"access_token": "fresh", "expires_in": 3600, "scope": "chatgpt.tokens.use.direct"}

    monkeypatch.setattr(store, "_refresh", fake_refresh)
    assert asyncio.run(store.get_access_token()) == "fresh"


def test_oauth_chat_provider_uses_responses_api(monkeypatch):
    calls = {}

    class FakeResponse:
        output_text = "hello"

    class FakeResponses:
        def create(self, **kwargs):
            calls.update(kwargs)
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.responses = FakeResponses()

    class FakeModule:
        OpenAI = FakeOpenAI

    monkeypatch.setitem(sys.modules, "openai", FakeModule())
    from llm_providers import _OpenAIOAuthChatProvider

    class Store:
        def get_access_token_sync(self):
            return "oauth-token"

    provider = _OpenAIOAuthChatProvider("gpt-5.4-mini", Store())
    assert provider.chat("gemma4:e4b", [{"role": "user", "content": "hi"}]) == "hello"
    assert calls["client"]["api_key"] == "oauth-token"
    assert calls["model"] == "gpt-5.4-mini"
    assert calls["store"] is False
