"""真实 DeepSeek API 最小冒烟测试。

用法（确保先配置环境变量）：
    DEEPSEEK_API_KEY=... DEEPSEEK_BASE_URL=https://api.deepseek.com \
    DEEPSEEK_MODEL=... .venv/bin/python scripts/smoke_test_ai.py

- 调用当前配置的 DEEPSEEK_MODEL
- 使用 response_format={"type":"json_object"}（json_mode=True，默认）
- system/user prompt 明确要求返回 JSON（满足 DeepSeek json_object 约束）
- 请求成功后解析 JSON 并校验 {"ok": true}
- 输出：API connectivity: OK / Model: ... / JSON output: OK
- 绝不打印 API Key
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许从任意工作目录运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai.client import DeepSeekClient  # noqa: E402


def main() -> int:
    client = DeepSeekClient()
    if not client.is_configured():
        print("DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL 未配置，无法进行真实 API 验证。")
        return 2

    # prompt 必须包含 "json" 字样，才能配合 response_format=json_object
    system_prompt = (
        "你是一个测试助手。你只能严格输出 JSON，不要输出任何其他文字。"
    )
    user_prompt = (
        "请按要求返回 JSON 对象：{\"ok\": true}。只输出该 JSON，不要解释。"
    )

    print(f"使用模型: {client.model}")
    try:
        content = client.chat(system_prompt, user_prompt, json_mode=True)
    except Exception as e:  # noqa: BLE001 - 冒烟测试统一报告失败原因
        print(f"REQUEST FAILED: {e}")
        return 1

    print("API connectivity: OK")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"JSON output: FAIL (无法解析模型返回: {e})")
        return 1

    if data.get("ok") is True:
        print("JSON output: OK")
        return 0
    print(f"JSON output: FAIL (返回内容不含 ok=true: {content})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
