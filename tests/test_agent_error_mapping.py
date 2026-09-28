"""Finite UI error taxonomy never echoes provider exception content."""
import pytest
from app.agent.errors import map_agent_error, SAFE_MESSAGES
from app.agent.memory.compactor import AgentContextTooLargeError
from app.agent.runtime import AgentRuntimeError
from app.ai.interface import AIServiceError


@pytest.mark.parametrize("error,configured,code", [
    (AIServiceError("SECRET_API_KEY https://user:pass@example.com"), False, "model_not_configured"),
    (AIServiceError("SECRET_API_KEY https://user:pass@example.com"), True, "model_connection_failed"),
    (AgentContextTooLargeError("SECRET_API_KEY"), True, "context_too_large"),
    (AgentRuntimeError("SECRET_API_KEY"), True, "runtime_failed"),
    (RuntimeError("SECRET_API_KEY"), None, "runtime_failed"),
])
def test_safe_mapper_classification_and_persisted_message_notice(error, configured, code):
    result = map_agent_error(error, model_configured=configured, user_message_persisted=True)
    assert result.code == code
    assert "你的消息已保存，但本轮没有生成回答" in result.message
    assert "SECRET_API_KEY" not in result.message
    assert "user:pass" not in result.message
    assert result.message.startswith(SAFE_MESSAGES[code])


def test_no_false_persistence_claim_before_accepted_user_turn():
    message = map_agent_error(RuntimeError("factory failed"),
                              user_message_persisted=False).message
    assert "已保存" not in message
