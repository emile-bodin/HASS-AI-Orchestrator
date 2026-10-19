"""Configured OAuth generation models must not reach local Ollama generation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
import llm_providers


@pytest.mark.parametrize("role", ["ORCHESTRATOR_MODEL", "SMART_MODEL", "FAST_MODEL", "DEEP_REASONING_MODEL", "DASHBOARD_MODEL"])
def test_effective_oauth_model_wins_local_role(monkeypatch, role):
    monkeypatch.setenv("LLM_PROVIDER", "openai_oauth")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv(role, "qwen-local")
    assert llm_providers.effective_chat_model("qwen-local") == "gpt-5.6-terra"


def test_ollama_effective_model_unchanged(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    assert llm_providers.effective_chat_model("gemma4:e4b") == "gemma4:e4b"


@pytest.mark.asyncio
async def test_orchestrator_legacy_generation_routes_oauth_not_ollama(monkeypatch, tmp_path):
    import orchestrator as module
    monkeypatch.setenv("LLM_PROVIDER", "openai_oauth")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("DASHBOARD_MODEL", "qwen-local")
    provider = SimpleNamespace(name="openai_oauth", chat=Mock(side_effect=[
        '{"tasks": []}', '{"response": "checked", "actions": []}', '<!doctype html><html>fixture</html>',
    ]))
    monkeypatch.setattr(module, "make_chat_provider", lambda **kw: provider)
    local = Mock()
    local.chat.side_effect = AssertionError("OAuth must never call Ollama generation")
    monkeypatch.setattr(module.ollama, "Client", lambda **kw: local)
    instance = module.Orchestrator(SimpleNamespace(connected=True, get_states=AsyncMock(return_value=[{"entity_id": "light.fixture", "state": "on", "attributes": {}}])), None, None, {}, model_name="gemma4:e4b")
    instance.dashboard_dir = tmp_path
    instance._genai_client = None
    assert instance.model_name == "gpt-5.6-terra"
    assert instance.dashboard_model_name == "gpt-5.6-terra"
    assert (await instance.plan({"home_state": {}}))["tasks"] == []
    assert (await instance.process_chat_request("check"))["response"] == "checked"
    assert "fixture" in await instance.generate_visual_dashboard("status")
    assert provider.chat.call_count == 3
    for call in provider.chat.call_args_list:
        assert call.kwargs.get("model", call.args[0] if call.args else None) == "gpt-5.6-terra"
    local.chat.assert_not_called()


def test_oauth_dashboard_model_does_not_use_local_override(monkeypatch, tmp_path):
    from dashboard_studio import DashboardStudio
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    studio = DashboardStudio(ha_client_provider=lambda: None, store_dir=tmp_path)
    assert studio._resolve_model("openai_oauth", "gemma4:e4b") == "gpt-5.6-terra"


@pytest.mark.parametrize("value, expected", [(False, "false"), (True, "true"), (None, "true"), ("missing", "true")])
@pytest.mark.parametrize("provider", ["ollama", "openai_oauth"])
def test_startup_jq_preserves_default_true_boolean_options(tmp_path, value, expected, provider):
    import json
    import subprocess
    from pathlib import Path
    config = {"llm_provider": provider, "openai_model": "gpt-5.6-terra"}
    if value != "missing":
        config.update(enable_rag=value, dry_run_mode=value)
    path = tmp_path / "options.json"
    path.write_text(json.dumps(config))
    source = (Path(__file__).parents[2] / "run.sh").read_text().split("# Start Ollama server")[0]
    source = source.replace('CONFIG_PATH="${CONFIG_PATH:-/data/options.json}"', f'CONFIG_PATH="{path}"')
    result = subprocess.run(["bash", "-c", source], text=True, capture_output=True, check=True)
    assert f"RAG Enabled: {expected}" in result.stdout
    assert f"Dry Run Mode: {expected}" in result.stdout
    for role in ("Orchestrator", "Smart", "Fast", "Deep Reasoning"):
        expected_model = "gpt-5.6-terra" if provider == "openai_oauth" else "gemma4:e4b"
        assert f"{role} Model: {expected_model}" in result.stdout
    assert json.loads(path.read_text()) == config


@pytest.mark.parametrize("options, expected", [({}, True), ({"flag": None}, True), ({"flag": False}, False), ({"flag": True}, True)])
def test_backend_default_true_boolean_matches_startup(options, expected):
    from runtime_config import default_true_option
    assert default_true_option(options, "flag") is expected


def test_oauth_explicit_model_cannot_be_overridden_by_local_or_extra_options(monkeypatch):
    from tests.test_oauth_responses_streaming import sync_provider, completed
    provider, requests, _ = sync_provider(monkeypatch, [completed()])
    provider.chat("qwen-local", [], extra_options={"model": "another-model"})
    assert requests[0]["model"] == "gpt-5.4-mini"


def test_tool_factory_oauth_configured_model_beats_local_role(monkeypatch):
    import openai_oauth
    import reasoning_harness
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setattr(openai_oauth, "OpenAIOAuthStore", lambda: SimpleNamespace(get_access_token_sync=lambda: "fixture"))
    factory = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(reasoning_harness, "OpenAIResponsesBackend", factory)
    llm_providers.make_tool_backend("openai_oauth", model="qwen-local")
    assert factory.call_args.kwargs["model"] == "gpt-5.6-terra"


def test_oauth_factory_failure_never_falls_back_to_ollama(monkeypatch):
    import openai_oauth
    monkeypatch.setattr(openai_oauth, "OpenAIOAuthStore", Mock(side_effect=RuntimeError("fixture unavailable")))
    with pytest.raises(RuntimeError):
        llm_providers.make_chat_provider("openai_oauth")
    with pytest.raises(RuntimeError):
        llm_providers.make_tool_backend("openai_oauth", model="gpt-5.6-terra")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name, expected", [("openai_oauth", "gpt-5.6-terra"), ("ollama", "qwen-local")])
async def test_specialist_effective_model_matches_generation(monkeypatch, tmp_path, provider_name, expected):
    from agents import base_agent
    monkeypatch.setenv("LLM_PROVIDER", provider_name)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    provider = SimpleNamespace(name=provider_name, chat=Mock(return_value="fixture answer"))
    local = Mock()
    local.chat.side_effect = AssertionError("No legacy Ollama generation")
    monkeypatch.setattr(base_agent, "make_chat_provider", lambda **kwargs: provider)
    monkeypatch.setattr(base_agent.ollama, "Client", lambda **kwargs: local)

    class Agent(base_agent.BaseAgent):
        async def decide(self, context):
            return {}

        async def gather_context(self):
            return {}

    agent = Agent("fixture", "Fixture", None, None, str(tmp_path / "skills.md"), model_name="qwen-local")
    assert agent.model_name == expected
    assert await agent._call_llm("status") == "fixture answer"
    assert provider.chat.call_args.args[0] == expected
    local.chat.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_cannot_send_remote_model_to_stale_local_generation_client(monkeypatch):
    from orchestrator import Orchestrator
    monkeypatch.setenv("LLM_PROVIDER", "openai_oauth")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    instance = Orchestrator.__new__(Orchestrator)
    instance.model_name = "gpt-5.6-terra"
    instance.llm_provider = SimpleNamespace(name="ollama")
    instance.llm_client = Mock()
    with pytest.raises(RuntimeError, match="OAuth"):
        await instance._generation_chat([])
    instance.llm_client.chat.assert_not_called()
