"""订阅连接测试：独立配置连接、刷新保护和真实端到端计时。"""
import time
from contextlib import nullcontext

from .bridge_pool import profile_bridge_key
from .config_service import AIConfigService, ConnectionTestResult
from .pi_ai_bridge import oauth_profile_lock


def run_oauth_test(provider, model, credential, *, timeout, db_path=None, profile_id=None, refreshed=None):
    from .pi_ai_bridge import PiAIBridge
    refreshed = refreshed if refreshed is not None else {}
    first_token_ms = None
    started = time.monotonic()
    service = AIConfigService(db_path=db_path) if db_path and profile_id is not None else None
    lock = oauth_profile_lock(db_path, profile_id) if service else nullcontext()
    with lock:
        if service:
            cfg = service.resolve_profile_config(profile_id)
            credential = cfg.oauth_credential
            if not cfg.is_configured:
                raise ValueError(cfg.error_message or "订阅配置无效")

        def capture(event):
            nonlocal first_token_ms
            if event.get("event") == "credential":
                updated = event.get("credential")
                if isinstance(updated, dict):
                    refreshed["value"] = updated
                    if service:
                        service.save_oauth_credential(profile_id, updated)
                        refreshed["persisted"] = True
            elif event.get("event") == "text_delta" and first_token_ms is None:
                first_token_ms = int((time.monotonic() - started) * 1000)

        response = PiAIBridge().complete(
            provider=provider, model=model, credential=credential,
            messages=[{"role": "user", "content": "Reply with OK."}],
            temperature=.3, max_tokens=64, on_event=capture, timeout=timeout,
            profile_key=profile_bridge_key(db_path, profile_id) if service else None,
        )
    content = (response.get("content") or "").strip()
    reason = str(response.get("finishReason") or "")
    error = str(response.get("errorMessage") or "").strip()
    failed = bool(error) or reason in {"error", "aborted"}
    message = error or (f"模型请求失败（{reason}）" if failed else "连接成功")
    if not failed and not content:
        kinds = ", ".join(response.get("contentTypes") or []) or "无可见内容"
        message = f"连接成功（服务已响应，但没有文本；stopReason={reason or '未知'}，内容类型={kinds}）"
    return ConnectionTestResult(
        ok=not failed, message=message, model=model, source=content[:50],
        latency_ms=int((time.monotonic() - started) * 1000), first_token_ms=first_token_ms,
        oauth_credential=response.get("credential") or refreshed.get("value"),
        credential_persisted=service is not None,
    )
