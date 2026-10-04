"""离线比较单次进程与复用进程的开销；不代表真实模型网络延迟。"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai.pi_ai_bridge import PiAIBridge
from app.ai.bridge_pool import POOL


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bridge = PiAIBridge()
    cold, warm = [], []
    try:
        for _ in range(5):
            start = time.perf_counter()
            bridge.call({"action": "catalog"})
            cold.append(round((time.perf_counter() - start) * 1000, 2))
        bridge.catalog()  # 预热，不计入连续请求测量。
        for _ in range(5):
            start = time.perf_counter()
            bridge.catalog()
            warm.append(round((time.perf_counter() - start) * 1000, 2))
    finally:
        POOL.close()
    report = {"scope": "offline_catalog_no_model_request", "single_process_ms": cold,
              "reused_process_ms": warm, "single_median_ms": statistics.median(cold),
              "reused_median_ms": statistics.median(warm)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
