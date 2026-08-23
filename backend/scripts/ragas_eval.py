"""
backend/scripts/ragas_eval.py

职责: RAGAS 量化评测 -- 纯向量 vs 混合检索三指标对比（Faithfulness / AR / Context Recall）

核心设计:
  为什么手动实现三指标而不用 ragas 包: ragas 包在 Python 3.14 上不可用
  （scikit-network 需 MSVC 编译 + langchain_community.vertexai 模块缺失），
  故手动实现 RAGAS 三指标定义，用 DeepSeek 作 LLM-as-judge，指标定义对齐 RAGAS。
  ground_truth 用人工标注的 REFERENCE_ANSWERS（eval_questions.md 的"预期"列只是检索
  预期描述，不能作 Context Recall 的 ground_truth），每题给出标准答案要点供覆盖度评判。

输入: docs/eval_questions.md（18 题）+ 知识库（Milvus/ES）
输出: docs/retrieval_comparison.md 追加 RAGAS 量化指标表 + 分类汇总 + 结论
用法: cd backend && .venv/Scripts/python.exe scripts/ragas_eval.py
前置: Docker 起 milvus + elasticsearch；.env 配 DEEPSEEK_API_KEY；知识库已上传
"""

import os
import sys
import logging
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

EVAL_QUESTIONS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "eval_questions.md")
COMPARISON_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "retrieval_comparison.md")
TENANT_ID = 1  # demo 租户
DEEPSEEK_MODEL = "deepseek/deepseek-chat"

# 人工标注的标准答案（Context Recall 的 ground_truth）
# ⚠️ eval_questions.md 的"预期"列是检索预期描述，不能作 RAGAS ground_truth；
# 这里给出每题的标准答案要点，用于 Context Recall 覆盖度评判。
REFERENCE_ANSWERS: dict[str, str] = {
    "Q1": "TK-20240001 工单的当前状态为待处理（open）。",
    "Q2": "订单 ORD-20240002 的金额为 599 元。",
    "Q3": "已支付订单可申请退款，退款金额不超过订单实付金额。",
    "Q4": "支持开具增值税专用发票，需提供开票信息。",
    "Q5": "套餐升级后立即生效。",
    "Q6": "用户可在后台账户设置中升级到更高级别的套餐。",
    "Q7": "已支付订单会安排发货，用户可关注发货状态。",
    "Q8": "用户可对已支付订单申请退款。",
    "Q9": "登录页面加载缓慢是已知问题，可反馈技术排查。",
    "Q10": "用户可降级到价格更低的套餐方案。",
    "Q11": "知识库无相关内容，无法回答该问题。",
    "Q12": "知识库无相关内容，无法回答该问题。",
    "Q13": "知识库无相关内容，无法回答该问题。",
    "Q14": "知识库无相关内容，无法回答该问题。",
    "Q15": "知识库无相关内容，无法回答该问题。",
    "Q16": "退款政策为已支付订单可申请退款且金额不超过实付；流程为提交退款申请后由人工审批。",
    "Q17": "用户可在工单系统中查看工单的处理进度和当前状态。",
    "Q18": "套餐费用较高时可选择分期付款或降级到更低价套餐。",
}


# ─── 解析评测集 ───
def parse_eval_questions(md_text: str) -> list[dict]:
    """解析 eval_questions.md 的 4 个表格为题目列表。

    Returns: [{"id": "Q1", "category": "精确匹配类", "question": "...", "expected": "..."}, ...]
    """
    questions = []
    current_category = None
    for line in md_text.splitlines():
        if line.startswith("## "):
            # 取标题中"类"结尾的类别名（去掉括号说明）
            header = line[3:].split("（")[0].strip()
            current_category = header
        elif line.startswith("|") and "|" in line[1:]:
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) >= 3 and cells[0].startswith("Q"):
                questions.append({
                    "id": cells[0],
                    "category": current_category or "未分类",
                    "question": cells[1],
                    "expected": cells[2],
                })
    return questions


# ─── LLM 生成回答 ───
def generate_answer(question: str, contexts: list[str]) -> str:
    """用 DeepSeek 基于给定 contexts 生成回答。"""
    import litellm

    ctx = "\n".join(contexts[:3]) if contexts else "（无检索结果）"
    prompt = (
        f"根据以下资料回答问题，仅基于资料内容，不要编造。\n\n"
        f"资料：\n{ctx}\n\n问题：{question}\n\n回答："
    )
    try:
        resp = litellm.completion(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200, temperature=0.0,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"generate_answer failed: {e}")
        return ""


# ─── RAGAS 三指标（LLM-as-judge，对齐 RAGAS 定义）───
def _parse_json_loose(text: str) -> dict | None:
    """宽松解析 LLM 返回的 JSON（容忍 ```代码块包裹/前后多余文本）。

    为什么需要: DeepSeek 常把 JSON 包在 ```json ... ``` 里，或在前后加解释文字，
    直接 json.loads 会失败；取第一个 '{' 到最后一个 '}' 的子串再解析更稳。
    """
    import json as _json
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return _json.loads(text[start:end + 1])
    except Exception:
        return None


def compute_metric_llm(metric: str, question: str, contexts: list[str],
                        answer: str, ground_truth: str) -> float:
    """用 LLM 评判单个 RAGAS 指标，返回 0-1 分数。

    - faithfulness: 回答是否忠实于 contexts（无幻觉）
    - answer_relevancy: 回答与问题的相关度
    - context_recall: contexts 覆盖 ground_truth 的程度

    为什么 context_recall 用 statement 覆盖法而非直接打分: 让 judge 直接返回 0-1
    数字噪声大（召回到要点却整体判 0）；RAGAS 原版做法是先把 ground_truth 拆成若干
    独立要点，逐个判断 context 是否覆盖，最后 recall = 覆盖要点数 / 总要点数，
    分步推理显著降低评判方差。
    """
    import litellm

    ctx = "\n".join(contexts[:3]) if contexts else "（无检索结果）"

    # context_recall: 分步推理（拆要点 → 逐个判覆盖 → 算 recall）
    if metric == "context_recall":
        prompt = (
            f"你是评估检索质量的专家。请按以下步骤评估 Context Recall：\n\n"
            f"Ground Truth（标准答案）：{ground_truth}\n"
            f"Context（参考资料）：\n{ctx}\n\n"
            f"步骤1：将 Ground Truth 拆分为若干独立的事实陈述（每句一个要点）。\n"
            f"步骤2：逐个判断每个要点是否能从 Context 中找到支持（能=1，不能=0）。\n"
            f"步骤3：仅返回一个 JSON，格式为 "
            f'{{"statements": ["要点1","要点2"], "covered": [1,0], "recall": 0.5}}，'
            f"其中 recall = covered 中 1 的个数除以总数。不要返回 JSON 以外的内容。"
        )
        try:
            resp = litellm.completion(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.0,
            )
            text = resp.choices[0].message.content.strip()
            data = _parse_json_loose(text)
            if data:
                covered = data.get("covered")
                if isinstance(covered, list) and len(covered) > 0:
                    return sum(1 for x in covered if x) / len(covered)
                if data.get("recall") is not None:
                    return max(0.0, min(1.0, float(data["recall"])))
            logger.warning(f"context_recall JSON parse failed: {text[:120]}")
            return 0.0
        except Exception as e:
            logger.warning(f"compute_metric_llm(context_recall) failed: {e}")
            return 0.0

    prompts = {
        "faithfulness": (
            f"问题：{question}\n参考资料：\n{ctx}\n回答：{answer}\n\n"
            f"请评估回答是否完全基于参考资料（不捏造资料外的信息）。"
            f"只返回一个0到1的小数，1=完全忠实，0=完全不忠实。只返回数字。"
        ),
        "answer_relevancy": (
            f"问题：{question}\n回答：{answer}\n\n"
            f"请评估回答与问题的相关程度（是否切题、是否有用）。"
            f"只返回一个0到1的小数，1=完全相关，0=完全不相关。只返回数字。"
        ),
    }
    try:
        resp = litellm.completion(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompts[metric]}],
            max_tokens=10, temperature=0.0,
        )
        text = resp.choices[0].message.content.strip().replace("，", "").replace(",", "")
        return max(0.0, min(1.0, float(text)))
    except Exception as e:
        logger.warning(f"compute_metric_llm({metric}) failed: {e}")
        return 0.0


# ─── 格式化结果表 ───
def format_results_table(results: list[dict]) -> str:
    """格式化 RAGAS 量化结果为 markdown 表。"""
    lines = [
        "## RAGAS 量化指标（规格 008）",
        "",
        "| # | 类型 | 方法 | Faithfulness | Answer Relevancy | Context Recall |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['q']} | {r['category']} | {r['method']} | "
            f"{r['faithfulness']:.2f} | {r['answer_relevancy']:.2f} | {r['context_recall']:.2f} |"
        )
    return "\n".join(lines)


def _category_summary(results: list[dict]) -> str:
    """按类别 × 方法汇总均值 + 结论。"""
    from collections import defaultdict
    agg = defaultdict(lambda: defaultdict(list))
    for r in results:
        key = (r["category"], r["method"])
        for m in ("faithfulness", "answer_relevancy", "context_recall"):
            agg[key][m].append(r[m])

    lines = ["", "### 分类汇总（均值）", "",
             "| 类型 | 方法 | Faithfulness | AR | CR |", "|---|---|---|---|---|"]
    for (cat, method), metrics in sorted(agg.items()):
        f = sum(metrics["faithfulness"]) / len(metrics["faithfulness"])
        ar = sum(metrics["answer_relevancy"]) / len(metrics["answer_relevancy"])
        cr = sum(metrics["context_recall"]) / len(metrics["context_recall"])
        lines.append(f"| {cat} | {method} | {f:.2f} | {ar:.2f} | {cr:.2f} |")

    # 混合 vs 纯向量 Context Recall 对比
    hybrid_cr = [r["context_recall"] for r in results if r["method"] == "hybrid"]
    vector_cr = [r["context_recall"] for r in results if r["method"] == "vector"]
    if hybrid_cr and vector_cr:
        h_avg = sum(hybrid_cr) / len(hybrid_cr)
        v_avg = sum(vector_cr) / len(vector_cr)
        diff_pp = (h_avg - v_avg) * 100
        lines.append("")
        lines.append(f"**结论**：混合检索 Context Recall 均值 {h_avg:.2f}，纯向量 {v_avg:.2f}，"
                     f"混合 - 纯向量 = {diff_pp:+.1f} 个百分点（SC-002 目标 ≥ +15pp）。")
    return "\n".join(lines)


# ─── 主流程 ───
def main():
    parser = argparse.ArgumentParser(description="运行纯向量与混合检索 RAGAS 对照评测")
    parser.add_argument(
        "--output",
        help="独立报告输出路径；省略时保持历史行为，追加到 retrieval_comparison.md",
    )
    args = parser.parse_args()

    from app.services.retrieval_service import hybrid_search

    with open(EVAL_QUESTIONS_PATH, encoding="utf-8") as f:
        questions = parse_eval_questions(f.read())
    logger.info(f"解析到 {len(questions)} 道评测题")

    results = []
    for q in questions:
        try:
            result = hybrid_search(q["question"], TENANT_ID)
            vector_ctx = [c.chunk_text for c in result.vector_results]
            hybrid_ctx = [c.chunk_text for c in result.hybrid_results]
        except Exception as e:
            logger.warning(f"Q {q['id']} retrieval failed: {e}")
            vector_ctx, hybrid_ctx = [], []

        for method, ctx in (("vector", vector_ctx), ("hybrid", hybrid_ctx)):
            answer = generate_answer(q["question"], ctx)
            ground_truth = REFERENCE_ANSWERS.get(q["id"], q["expected"])
            f = compute_metric_llm("faithfulness", q["question"], ctx, answer, ground_truth)
            ar = compute_metric_llm("answer_relevancy", q["question"], ctx, answer, ground_truth)
            cr = compute_metric_llm("context_recall", q["question"], ctx, answer, ground_truth)
            results.append({
                "q": q["id"], "category": q["category"], "method": method,
                "faithfulness": f, "answer_relevancy": ar, "context_recall": cr,
            })
            logger.info(f"  {q['id']} {method}: F={f:.2f} AR={ar:.2f} CR={cr:.2f}")

    # 可写独立报告用于版本对照；未指定时追加，不破坏已有 Top1 定性表。
    table = format_results_table(results)
    summary = _category_summary(results)
    output_path = args.output or COMPARISON_PATH
    if args.output:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# 口语化查询改写后 RAGAS 对照报告\n\n" + table + summary + "\n")
        logger.info(f"RAGAS 量化结果已写入 {output_path}")
    else:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write("\n\n" + table + summary + "\n")
        logger.info(f"RAGAS 量化结果已追加到 {output_path}")


if __name__ == "__main__":
    main()
