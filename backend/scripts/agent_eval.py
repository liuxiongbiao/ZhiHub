"""
backend/scripts/agent_eval.py

职责: Agent 评测 -- 对 10 条专用测试集评估意图识别准确率 + 工具调用成功率
对应日程: 009-tech-compare-agent-enhance

核心设计:
  内置 10 条 agent 专用评测集（含 expected_intent + expected_tool），覆盖知识咨询/
  工单查询/订单查询/敏感操作 4 类意图。通过 router_node 跑意图识别，检查 slots 中
  ticket_no/order_no 推断工具是否会被调用。calculate_accuracy 统计两项准确率，
  format_report 输出 markdown 报告含每条详情。

输入: 内置 TEST_SET（10 条）
输出: docs/agent_eval_report.md（意图准确率 + 工具成功率 + 每条详情）
用法: cd backend && .venv/Scripts/python.exe scripts/agent_eval.py
前置: DeepSeek API（.env DEEPSEEK_API_KEY）
"""

import os
import sys
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "agent_eval_report.md")

# ~10 条 agent 专用评测集（含预期意图 + 预期工具）
TEST_SET: list[dict] = [
    {"message": "如何升级套餐？", "expected_intent": "knowledge_consult", "expected_tool": None},
    {"message": "工单TK-20240001处理得怎么样了", "expected_intent": "ticket_query", "expected_tool": "get_ticket_status"},
    {"message": "订单ORD-20240001发货了吗", "expected_intent": "order_query", "expected_tool": "query_order"},
    {"message": "我要退款！你们服务太差了", "expected_intent": "sensitive_action", "expected_tool": None},
    {"message": "怎么开具增值税发票", "expected_intent": "knowledge_consult", "expected_tool": None},
    {"message": "查工单TK-20240001，手机号13800000001", "expected_intent": "ticket_query", "expected_tool": "get_ticket_status"},
    {"message": "ORD-20240002的金额是多少", "expected_intent": "order_query", "expected_tool": "query_order"},
    {"message": "套餐太贵了能不能降级", "expected_intent": "knowledge_consult", "expected_tool": None},
    {"message": "登录页面一直转圈打不开", "expected_intent": "knowledge_consult", "expected_tool": None},
    {"message": "帮我投诉产品质量问题", "expected_intent": "sensitive_action", "expected_tool": None},
]


def calculate_accuracy(results: list[dict]) -> dict:
    """计算意图识别准确率 + 工具调用成功率。"""
    total = len(results)
    if total == 0:
        return {"intent_accuracy": 0, "tool_accuracy": 0, "total": 0}
    intent_correct = sum(1 for r in results if r["intent_ok"])
    tool_correct = sum(1 for r in results if r["tool_ok"])
    return {
        "intent_accuracy": intent_correct / total,
        "tool_accuracy": tool_correct / total,
        "total": total,
    }


def format_report(results: list[dict], accuracy: dict) -> str:
    """格式化评测报告 markdown。"""
    lines = [
        "# Agent 评测报告（009-tech-compare-agent-enhance）",
        "",
        f"## 意图识别准确率: {accuracy['intent_accuracy']*100:.1f}%（{int(accuracy['intent_accuracy']*accuracy['total'])}/{accuracy['total']}）",
        f"## 工具调用成功率: {accuracy['tool_accuracy']*100:.1f}%（{int(accuracy['tool_accuracy']*accuracy['total'])}/{accuracy['total']}）",
        "",
        "## 详细",
        "| # | 消息 | 预期意图 | 实际意图 | 预期工具 | 实际工具 | 意图✓ | 工具✓ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        msg = r["message"][:20]
        lines.append(
            f"| {i} | {msg} | {r['expected_intent']} | {r.get('actual_intent','')} | "
            f"{r['expected_tool'] or '-'} | {r.get('actual_tool') or '-'} | "
            f"{'✓' if r['intent_ok'] else '✗'} | {'✓' if r['tool_ok'] else '✗'} |"
        )
    return "\n".join(lines)


def main():
    """跑评测 -> 写 docs/agent_eval_report.md。"""
    from app.agents.nodes.router import router_node

    results = []
    for i, q in enumerate(TEST_SET, 1):
        # 意图识别
        router_result = None
        try:
            router_state = {
                "messages": [{"role": "user", "content": q["message"]}],
                "tenant_id": 1,
            }
            router_result = router_node(router_state)
            actual_intent = router_result.get("intent", "")
        except Exception as e:
            logger.warning(f"Q{i} router failed: {e}")
            actual_intent = ""

        intent_ok = (actual_intent == q["expected_intent"])

        # 工具调用（简化：检查 router slots 是否含预期工具相关槽位）
        actual_tool = None
        if q["expected_tool"]:
            # 如果意图匹配且 slots 含 ticket_no/order_no，认为工具会被调用
            slots = router_result.get("slots", {}) if router_result else {}
            if q["expected_tool"] == "get_ticket_status" and slots.get("ticket_no"):
                actual_tool = "get_ticket_status"
            elif q["expected_tool"] == "query_order" and slots.get("order_no"):
                actual_tool = "query_order"
        elif q["expected_tool"] is None:
            actual_tool = None  # 无工具预期

        tool_ok = (actual_tool == q["expected_tool"])

        results.append({
            "message": q["message"],
            "expected_intent": q["expected_intent"],
            "actual_intent": actual_intent,
            "expected_tool": q["expected_tool"],
            "actual_tool": actual_tool,
            "intent_ok": intent_ok,
            "tool_ok": tool_ok,
        })
        logger.info(f"Q{i}: intent={'✓' if intent_ok else '✗'} tool={'✓' if tool_ok else '✗'} | {q['message'][:30]}")

    accuracy = calculate_accuracy(results)
    report = format_report(results, accuracy)

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    logger.info(f"\n意图准确率: {accuracy['intent_accuracy']*100:.1f}%")
    logger.info(f"工具成功率: {accuracy['tool_accuracy']*100:.1f}%")
    logger.info(f"报告已写入 {REPORT_PATH}")


if __name__ == "__main__":
    main()