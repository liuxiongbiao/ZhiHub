"""
backend/app/services/memory_service.py

职责: 长期记忆--LLM 抽取关键事实 -> 向量化 -> Milvus 存储/检索，跨会话记住用户偏好。

核心设计:
  三步：①extract_facts 用 LangChain 结构化输出链从用户消息抽取值得长期记住的事实（套餐/手机号/昵称/偏好）；
  ②save_memory 把事实向量化存入 Milvus user_memories collection，按 tenant+user+fact_type 先删后插实现 upsert；
  ③search_memory 用当前 query 向量检索相关记忆，相似度 >= 阈值才返回。
  全程降级设计：抽取/保存/检索任一失败都返回空/不抛，记忆是"锦上添花"不能拖垮主对话。

关键技术点:
  - LangChain 结构化输出: with_structured_output(list[Fact])，LLM 返回自动校验为 Fact 列表，无需手动 JSON 解析
  - upsert 语义: 按 tenant+user+fact_type 先 delete 再 insert，实现"套餐升级覆盖旧值"
  - 相似度阈值 0.35: 从 0.5 降下来，因为记忆 fact 内容短、表述差异大（用户说"我是高级套餐"vs存"plan=pro"），
    0.5 太严会漏召回关键偏好，0.35 提升召回率；记忆场景宁可多召回几条交 LLM 判断，也不要漏掉用户偏好
  - 降级策略: 失败返回空列表/不抛异常，记忆缺失不影响主对话

依赖关系:
  - 上游: app/services/agent_service.py（对话后 extract_facts+save_memory 抽取记忆，对话前 search_memory 注入记忆）
  - 下游: app/core/embedding_client.py（embed + Milvus memory collection）、langchain_openai（结构化抽取）
"""

import logging
import re
import time

from pydantic import BaseModel
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

MEMORY_TOP_K = 3       # 检索记忆条数
MEMORY_THRESHOLD = 0.35  # 相似度阈值，低于此值的记忆不注入
# 记忆事实类型白名单: fact_type 由 LLM 产出（不可信），
# 注入 Milvus 删除表达式前必须白名单校验，防诱导输出引号/or 扩大删除范围
ALLOWED_FACT_TYPES = frozenset({"plan", "phone", "nickname", "preference"})

# user_id 安全校验（M10）: user_id 直接拼进 Milvus 表达式（带引号），若含引号/空格/and|or 等
# 可注入表达式扩大删除/检索范围（跨用户甚至跨租户）。end_user_id 是访客标识，正常仅字母数字._-。
_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _is_safe_user_id(user_id) -> bool:
    """user_id 是否仅含安全字符（防 Milvus 表达式注入）。不安全则跳过记忆读写。

    user_id 可能是 int（调用方传数字 id）或 str（end_user_id 访客标识），统一 str 化校验。
    """
    if user_id is None:
        return False
    return bool(_SAFE_USER_ID_RE.match(str(user_id)))


# ─── 结构化输出模型 ───
class Fact(BaseModel):
    """记忆事实结构（LangChain 保证 LLM 返回符合此结构）。

    fact_type: 事实类型（plan/phone/nickname/preference 等），用于 upsert 时按类型覆盖
    content: 事实内容
    """
    fact_type: str
    content: str


class FactList(BaseModel):
    """事实列表包装：function_calling 的 tools schema 需要 object 根节点，
    LangChain 不接受裸 list[Model] 作为结构化输出 schema（实测报错）。"""

    facts: list[Fact]


# ─── LangChain 结构化输出链（模块级惰性单例）───
# with_structured_output(FactList) 是核心：LangChain 在 LLM 返回后自动校验为 FactList，
# 类型安全，无需手动剥围栏/解析 JSON/处理格式异常（自 litellm+围栏剥离方案迁移而来）。
# 惰性构建：settings 的 API Key 由 .env 注入，且避免测试/无 LLM 环境 import 即初始化模型客户端。
_extract_agent = None  # ChatOpenAI.with_structured_output(FactList) 实例

_EXTRACT_SYSTEM_PROMPT = (
    "你是记忆抽取器。从用户消息中抽取值得长期记住的持久事实，"
    "如：套餐（plan）、手机号（phone）、用户称呼/昵称（nickname）、"
    "回答偏好（preference）等。\n"
    "没有可记住的事实则返回空列表。"
)


def _get_extract_agent():
    """获取/构建记忆抽取结构化输出链（惰性单例）。"""
    global _extract_agent
    if _extract_agent is None:
        from app.config import settings

        model_name = settings.llm_primary_model.split("/", 1)[-1]
        llm = ChatOpenAI(
            model=model_name,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=0.0,   # 抽取要确定性
            timeout=60,        # 与 llm_client.LLM_TIMEOUT_SECONDS 对齐
            max_retries=1,
        )
        # DeepSeek 不支持 response_format=json_schema 严格模式，显式 function_calling
        # （tools 机制），与 router.py 保持一致。
        _extract_agent = llm.with_structured_output(FactList, method="function_calling")
    return _extract_agent


def extract_facts(user_message: str | None) -> list[dict]:
    """LLM 抽取关键用户事实 -> [{fact_type, content}]，无则空。

    流程:
      1. 空消息 -> 返回空
      2. LangChain 结构化输出链 invoke 抽取（with_structured_output(list[Fact]) 自动校验）
      3. 转换为 dict 列表，过滤掉 content 为空的
      4. 失败降级返回空列表

    设计决策:
      用 LangChain 结构化输出而非裸 litellm + 手动 JSON 解析，是因为 LLM 返回格式不稳定
      （可能带 ```json 围栏、可能多返/少返字段），手动解析需大量防御代码且易错。
      with_structured_output(list[Fact]) 让 SDK 在 function calling 层做校验，类型安全。
      invoke 是同步调用（项目整体同步架构，见 agent_service 说明）。
      失败降级返回空列表。
    """
    if not user_message:
        return []

    try:
        # 规格 013: async 端点内调用会触发事件循环线程问题；run_sync_in_thread 保证任意上下文
        # 都走成功路径（无 loop 时直接调用，零开销）。
        from app.utils.asyncio_compat import run_sync_in_thread

        messages = [
            {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        result = run_sync_in_thread(_get_extract_agent().invoke, messages)
        # 防御：正常返回 FactList；测试 mock 可能直接返回 list
        items = result.facts if isinstance(result, FactList) else result
        return [
            {"fact_type": f.fact_type, "content": f.content}
            for f in items
            if f.content
        ]
    except Exception as e:
        # 降级：抽取失败不影响主对话，返回空
        logger.warning(f"extract_facts failed (degraded): {e}")
        return []


def _upsert_memory_fact(collection, tenant_id: int, user_id: str, fact: dict) -> None:
    """按租户、用户和事实类型覆盖一条记忆。类型不在白名单或内容为空时跳过。"""
    if fact.get("fact_type") not in ALLOWED_FACT_TYPES:
        logger.warning(f"Memory fact_type not in whitelist, skipped: {fact.get('fact_type')!r}")
        return
    from app.core.embedding_client import embed_texts
    from app.utils.desensitize import mask_pii

    content = mask_pii(fact.get("content"))
    if not content:
        return
    expr = (
        f'tenant_id == {tenant_id} and user_id == "{user_id}" '
        f'and fact_type == "{fact["fact_type"]}"'
    )
    try:
        collection.delete(expr)
    except Exception:
        pass
    vectors = embed_texts([content])
    if vectors:
        collection.insert([
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "fact_type": fact["fact_type"],
                "content": content,
                "vector": vectors[0],
                "created_at": float(time.time()),
            }
        ])


def save_memory(tenant_id: int, user_id: str, facts: list[dict]) -> None:
    """embed content -> upsert Milvus memory_collection。

    流程:
      1. 初始化 memory collection（幂等）
      2. 对每条 fact：按 tenant+user+fact_type 删旧 -> embed -> 插新
      3. flush 保证落盘

    设计决策:
      按 tenant+user+fact_type 先删后插实现 upsert，是为了"套餐升级覆盖旧值"--用户从 basic 升到 pro，
      旧的 plan=basic 要被新的 plan=pro 覆盖，而非并存两条。先 delete 同类型再 insert 是最简单的
      upsert 实现（Milvus 不支持原生 upsert by expr）。失败降级不抛（FR-008），记忆写失败不影响主流程。

    按 tenant_id+user_id+fact_type 先删旧同类型再插新（套餐升级覆盖旧值）。
    失败降级不抛（FR-008）。
    """
    if not facts:
        return
    # M10: user_id 不安全（含引号/or 等可注入 Milvus 表达式）→ 跳过写入
    if not _is_safe_user_id(user_id):
        logger.warning(f"Memory save skipped: unsafe user_id {user_id!r}")
        return

    try:
        from app.core.embedding_client import (
            get_memory_collection,
            init_memory_collection,
        )

        init_memory_collection()
        collection = get_memory_collection()

        for fact in facts:
            _upsert_memory_fact(collection, tenant_id, user_id, fact)
        # flush 把缓冲区数据落盘，保证后续检索能查到
        collection.flush()
        logger.info(f"Saved {len(facts)} memories for user {user_id}")
    except Exception as e:
        # 降级：保存失败不抛，记忆缺失不影响主对话
        logger.warning(f"save_memory failed (degraded): {e}")


def _memory_contents(results, threshold: float) -> list[str]:
    memories = []
    for hit in results[0]:
        if hit.score >= threshold:
            content = hit.entity.get("content", "")
            if content:
                memories.append(content)
    return memories


def search_memory(
    tenant_id: int, user_id: str, query: str,
    top_k: int = MEMORY_TOP_K, threshold: float = MEMORY_THRESHOLD,
) -> list[str]:
    """embed query -> Milvus search -> 相似度>=threshold 的 content 列表。

    流程:
      1. 空 query -> 返回空
      2. 初始化 collection + query 向量化
      3. Milvus search（按 tenant+user 过滤，IP 度量，nprobe=10）
      4. 相似度 >= threshold 的 content 收集返回

    设计决策:
      阈值 0.35 低于知识库检索的常用阈值，因为记忆 fact 内容短、表述差异大（用户问"我什么套餐"
      vs 存的"plan=pro"），高阈值会漏召回。记忆场景宁可多召回几条交 LLM 判断，也不要漏掉用户偏好。
      按 tenant+user 过滤保证记忆隔离--用户只能检索到自己的记忆。

    失败降级返回空（FR-008）。
    """
    if not query:
        return []
    # M10: user_id 不安全（含引号/or 等可注入 Milvus 表达式）→ 跳过检索
    if not _is_safe_user_id(user_id):
        logger.warning(f"Memory search skipped: unsafe user_id {user_id!r}")
        return []

    try:
        from app.core.embedding_client import (
            embed_texts,
            get_memory_collection,
            init_memory_collection,
        )

        init_memory_collection()
        collection = get_memory_collection()

        vectors = embed_texts([query])
        if not vectors:
            return []

        results = collection.search(
            data=[vectors[0]],
            anns_field="vector",
            param={"metric_type": "IP", "params": {"nprobe": 10}},
            limit=top_k,
            # 按 tenant+user 过滤，记忆隔离
            expr=f'tenant_id == {tenant_id} and user_id == "{user_id}"',
            output_fields=["content"],
        )

        memories = _memory_contents(results, threshold)
        return memories
    except Exception as e:
        # 降级：检索失败返回空，主对话无记忆注入也能继续
        logger.warning(f"search_memory failed (degraded): {e}")
        return []
