"""18 题逐题新会话端到端评测，验证自动回答、反问与转人工行为。

默认输出 docs/e2e_resolution_report_after_rewrite.md，并清理本轮 MySQL/Mongo/
Redis/长期记忆测试数据；加 --keep-data 可保留现场供排查。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.crud import session as session_crud  # noqa: E402
from app.services import session_service  # noqa: E402
from app.services.agent_service import handle_message  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_PATH = ROOT / "docs" / "eval_questions.md"
DEFAULT_REPORT = ROOT / "docs" / "e2e_resolution_report_after_rewrite.md"
TENANT_ID = 1

EXPECTED = {
    "Q1": "CLARIFY",
    "Q2": "CLARIFY",
    "Q3": "ANSWERED",
    "Q4": "ANSWERED",
    "Q5": "ANSWERED",
    "Q6": "ANSWERED",
    "Q7": "ANSWERED",
    "Q8": "HANDOFF",  # 退款意图按安全策略强制人工
    "Q9": "ANSWERED",
    "Q10": "ANSWERED",
    "Q11": "HANDOFF",
    "Q12": "HANDOFF",
    "Q13": "HANDOFF",
    "Q14": "HANDOFF",
    "Q15": "HANDOFF",
    "Q16": "ANSWERED",
    "Q17": "CLARIFY",  # 缺工单号，先反问
    "Q18": "ANSWERED",
}


def _parse_questions() -> list[dict]:
    questions: list[dict] = []
    category = "未分类"
    for line in QUESTIONS_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            category = line[3:].split("（", 1)[0].strip()
        elif line.startswith("| Q"):
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            if len(cells) >= 3:
                questions.append({"id": cells[0], "question": cells[1], "category": category})
    return questions


def _classify(status: str, response: dict) -> str:
    if status == "waiting_human":
        return "HANDOFF"
    if response.get("sources"):
        return "ANSWERED"
    content = response.get("content", "")
    if any(marker in content for marker in ("请提供", "请补充", "需要您提供")):
        return "CLARIFY"
    return "EMPTY"


def _md_cell(value: object, limit: int = 180) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")[:limit]


def _cleanup(session_ids: list[int], run_prefix: str) -> None:
    # 等待非关键 trace/记忆后台线程落库后再清理，避免清理完成后又出现残留。
    time.sleep(2)
    db = SessionLocal()
    try:
        from scripts.cleanup_eval_data import _purge_sessions

        _purge_sessions(db, session_ids, True)
        db.commit()
    finally:
        db.close()
    for session_id in session_ids:
        session_service.clear_context(session_id)
    try:
        from app.core.embedding_client import get_memory_collection

        collection = get_memory_collection()
        collection.delete(expr=f'user_id like "{run_prefix}%"')
        collection.flush()
    except Exception as exc:
        print(f"WARN: 长期记忆清理失败：{exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 18 题端到端自动解决率评测")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()

    questions = _parse_questions()
    if len(questions) != 18:
        raise RuntimeError(f"评测集应为 18 题，实际 {len(questions)} 题")

    run_prefix = "eval-rewrite-" + datetime.now().strftime("%Y%m%d%H%M%S")
    results: list[dict] = []
    session_ids: list[int] = []
    db = SessionLocal()
    try:
        for index, item in enumerate(questions, 1):
            created = session_service.create_session(
                db,
                tenant_id=TENANT_ID,
                end_user_id=f"{run_prefix}-{item['id'].lower()}",
            )
            session_id = created["session_id"]
            session_ids.append(session_id)
            response = handle_message(
                db,
                session_id=session_id,
                tenant_id=TENANT_ID,
                user_message=item["question"],
            )
            db.expire_all()
            current = session_crud.get_by_id(db, session_id, TENANT_ID)
            actual = _classify(current.status, response)
            expected = EXPECTED[item["id"]]
            result = {
                **item,
                "intent": (response.get("metadata") or {}).get("intent", ""),
                "status": current.status,
                "actual": actual,
                "expected": expected,
                "passed": actual == expected,
                "elapsed_ms": (response.get("metadata") or {}).get("elapsed_ms", 0),
                "confidence": (response.get("metadata") or {}).get("confidence", 0),
                "sources": len(response.get("sources") or []),
                "content": response.get("content", ""),
            }
            results.append(result)
            print(
                f"[{index:02d}/18] {item['id']} {actual} "
                f"({'PASS' if result['passed'] else 'FAIL'}) "
                f"{result['elapsed_ms']}ms sources={result['sources']}",
                flush=True,
            )
    finally:
        db.close()

    counts = {kind: sum(r["actual"] == kind for r in results) for kind in ("ANSWERED", "HANDOFF", "CLARIFY", "EMPTY")}
    passed = sum(r["passed"] for r in results)
    outside_ok = sum(r["passed"] for r in results if r["id"] in {"Q11", "Q12", "Q13", "Q14", "Q15"})
    q9_q10_ok = all(r["passed"] and r["sources"] > 0 for r in results if r["id"] in {"Q9", "Q10"})

    lines = [
        "# 端到端自动解决率评测报告（口语化改写后）",
        "",
        f"> 执行时间: {datetime.now():%Y-%m-%d %H:%M:%S} | tenant {TENANT_ID} 逐题新会话调 handle_message | 检索阈值: 0.3",
        "",
        "## 汇总",
        "",
        f"- 总题数: {len(results)}",
        f"- 自动回答 / 转人工 / 反问 / 空结果: {counts['ANSWERED']} / {counts['HANDOFF']} / {counts['CLARIFY']} / {counts['EMPTY']}",
        f"- 期望行为符合率: **{passed}/{len(results)} = {passed / len(results):.1%}**",
        f"- Q9/Q10 带引用回答: **{'PASS' if q9_q10_ok else 'FAIL'}**",
        f"- 知识库外 Q11-Q15 正确转人工: **{outside_ok}/5**",
        "",
        "## 逐题明细",
        "",
        "| # | 类别 | 问题 | 意图 | 状态 | 实际 | 期望 | 符合 | 耗时ms | 置信度 | 引用 | 回复摘要 |",
        "|---|---|---|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for result in results:
        lines.append(
            f"| {result['id']} | {_md_cell(result['category'])} | {_md_cell(result['question'])} | "
            f"{result['intent']} | {result['status']} | {result['actual']} | {result['expected']} | "
            f"{'✅' if result['passed'] else '❌'} | {result['elapsed_ms']} | {result['confidence']} | "
            f"{result['sources']} | {_md_cell(result['content'])} |"
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告：{args.report}")

    if not args.keep_data:
        _cleanup(session_ids, run_prefix)
        print(f"清理：本轮 {len(session_ids)} 个会话及关联数据已删除")

    return 0 if passed == len(results) and q9_q10_ok and outside_ok == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
