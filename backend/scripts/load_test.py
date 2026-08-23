"""PRD 4.1 性能验收压测脚本（轻量 HTTP 基准）。

用法（后端已通过 docker compose 启动后执行）:
    cd backend
    .venv/Scripts/python.exe scripts/load_test.py --base-url http://localhost:8080 \
        --concurrency 20 --rounds 200 --endpoint /health

带登录态测试受保护端点:
    .venv/Scripts/python.exe scripts/load_test.py --base-url http://localhost:8080 \
        --token <JWT> --endpoint /api/v1/session/list

输出: 各分位耗时（P50/P95/P99）、均值、吞吐（req/s）。
LLM/embedding 依赖外部服务，端到端知识问答指标需配置真实 API Key 后另测；
本脚本覆盖 PRD 4.1 中可复现的 HTTP 链路指标（混合检索/MCP 走业务端点时同样适用）。
"""

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * p / 100))
    return ordered[idx]


def _run_once(base_url: str, endpoint: str, token: str | None) -> tuple[float, int]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    start = time.perf_counter()
    resp = httpx.get(f"{base_url}{endpoint}", headers=headers, timeout=30.0)
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed, resp.status_code


def main() -> None:
    parser = argparse.ArgumentParser(description="PRD 4.1 性能基准压测")
    parser.add_argument("--base-url", default="http://localhost:8080", help="后端地址")
    parser.add_argument("--endpoint", default="/health", help="被测端点")
    parser.add_argument("--concurrency", type=int, default=20, help="并发数")
    parser.add_argument("--rounds", type=int, default=200, help="总请求数")
    parser.add_argument("--token", default=None, help="可选 Bearer JWT（测受保护端点）")
    args = parser.parse_args()

    latencies: list[float] = []
    statuses: dict[int, int] = {}
    start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(_run_once, args.base_url, args.endpoint, args.token)
            for _ in range(args.rounds)
        ]
        for fut in as_completed(futures):
            elapsed, code = fut.result()
            latencies.append(elapsed)
            statuses[code] = statuses.get(code, 0) + 1

    total = time.perf_counter() - start
    print(f"\nPRD 4.1 性能基准: {args.endpoint}")
    print(f"  环境: {args.base_url} | 并发 {args.concurrency} | 请求数 {args.rounds}")
    print(f"  P50: {_percentile(latencies, 50):.1f} ms")
    print(f"  P95: {_percentile(latencies, 95):.1f} ms")
    print(f"  P99: {_percentile(latencies, 99):.1f} ms")
    print(f"  Mean: {statistics.mean(latencies):.1f} ms")
    print(f"  Throughput: {args.rounds / total:.1f} req/s")
    print(f"  状态码分布: {statuses}")


if __name__ == "__main__":
    main()
