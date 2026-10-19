"""Regressions: empty model output must never complete a goal successfully."""
from types import SimpleNamespace

import pytest

from openai_response_stream import completed_response, response_output
from reasoning_harness import LLMResponse, ReasoningHarness, ToolRegistry, OpenAIResponsesBackend


@pytest.mark.asyncio
async def test_mapping_response_preserves_text_usage_and_continuation():
    snapshot = {
        "id": "resp_dict", "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Lights checked."}]}],
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }
    backend = OpenAIResponsesBackend(api_key="test-key", store=True)
    await backend._client.close()

    async def create(**kwargs):
        return snapshot

    backend._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    result = await backend.chat([{"role": "user", "content": "check"}], [])
    assert result.content == "Lights checked."
    assert result.usage["input_tokens"] == 12
    assert result.continuation["previous_response_id"] == "resp_dict"
    assert result.stop_reason == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_model,local_model", [("gpt-5.6-terra", "gemma4:e4b"), (None, "qwen-local")])
async def test_oauth_agent_selects_openai_model_and_logs_actual_backend(monkeypatch, caplog, explicit_model, local_model):
    import logging
    from agents.deep_reasoning_agent import DeepReasoningAgent
    import openai_oauth

    class Store:
        def get_access_token_sync(self):
            return "test-token"

    monkeypatch.setattr(openai_oauth, "OpenAIOAuthStore", Store)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4-mini")
    with caplog.at_level(logging.INFO):
        agent = DeepReasoningAgent(local_mcp=SimpleNamespace(tools={}), provider="openai_oauth",
                                   openai_model=explicit_model, ollama_model=local_model)
    try:
        expected = explicit_model or "gpt-5.4-mini"
        assert isinstance(agent.llm, OpenAIResponsesBackend)
        assert agent.llm.model == expected
        assert f"backend ({expected})" in caplog.text
        assert local_model not in caplog.text
    finally:
        await agent.llm._client.close()


def test_empty_wire_output_cannot_be_masked_by_stale_convenience_text():
    with pytest.raises(RuntimeError, match="empty"):
        response_output({"status": "completed", "output": [], "output_text": "stale text"})


def test_mapping_completed_snapshot_is_preserved():
    snapshot = {"id": "resp_dict", "status": "completed", "output": []}
    assert completed_response({"type": "response.completed", "response": snapshot}) is snapshot


@pytest.mark.parametrize("content", ["", " \n\t"])
def test_empty_response_is_not_a_usable_final(content):
    assert LLMResponse(content=content).is_final is False


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", " \n\t"])
async def test_empty_final_is_an_llm_error(content):
    class EmptyLLM:
        name = "empty"

        async def chat(self, messages, tools):
            return LLMResponse(content=content, usage={"output_tokens": 11})

    result = await ReasoningHarness(
        llm=EmptyLLM(), tools=ToolRegistry(), system_prompt="test",
    ).run("get all light statusses")
    assert result.stopped_reason == "llm_error"
    assert result.answer.strip()
    assert "empty" in result.answer.lower()
    assert result.tool_calls == 0
    assert result.usage["output_tokens"] == 11
