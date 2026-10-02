"""Security regressions for the Python-to-pi-ai OAuth bridge."""

from __future__ import annotations

import io
import json

import pytest

from app.ai.pi_ai_bridge import PiAIBridge, PiAIBridgeError


class _FakeProcess:
    def __init__(self, output: dict | list[dict]):
        self.stdin = io.StringIO()
        outputs = output if isinstance(output, list) else [output]
        self.stdout = io.StringIO("".join(json.dumps(item) + "\n" for item in outputs))
        self.stderr = io.StringIO()
        self.return_code = None

    def wait(self, timeout=None):
        self.return_code = 0
        return self.return_code

    def poll(self):
        return self.return_code

    def kill(self):
        self.return_code = -9


def _bridge_with_output(monkeypatch, output: dict | list[dict]) -> PiAIBridge:
    process = _FakeProcess(output)
    monkeypatch.setattr(PiAIBridge, "is_installed", staticmethod(lambda: True))
    monkeypatch.setattr(
        "app.ai.pi_ai_bridge.subprocess.Popen", lambda *args, **kwargs: process
    )
    return PiAIBridge()


def test_bridge_preserves_oauth_credential_and_scrubs_display_error(monkeypatch):
    access = "access-token-value-that-must-remain-exact"
    refresh = "refresh-token-value-that-must-remain-exact"
    credential = {
        "type": "oauth",
        "access": access,
        "refresh": refresh,
        "expires": 1_900_000_000_000,
        "accountId": "test-account-id",
    }
    bridge = _bridge_with_output(monkeypatch, [
        {"event": "credential", "credential": credential},
        {
            "result": {
                "credential": credential,
                "errorMessage": f"provider rejected bearer {access}; refresh={refresh}",
            },
        },
    ])
    events = []

    response = bridge.call(
        {"action": "complete", "credential": credential}, on_event=events.append
    )

    assert response["credential"] == credential
    assert events[0]["credential"] == credential
    assert access not in response["errorMessage"]
    assert refresh not in response["errorMessage"]
    assert "***" in response["errorMessage"]


def test_bridge_scrubs_process_errors(monkeypatch):
    access = "access-token-value-that-must-not-appear-in-errors"
    credential = {"type": "oauth", "access": access, "refresh": "refresh-token"}
    bridge = _bridge_with_output(monkeypatch, {
        "error": f"authorization failed for {access}",
    })

    with pytest.raises(PiAIBridgeError) as exc:
        bridge.call({"action": "complete", "credential": credential})

    assert access not in str(exc.value)
    assert "***" in str(exc.value)
