"""
backend/app/services/memory_service.py

职责: 长期记忆--LLM 抽取关键事实 -> 向量化 -> Milvus 存储/检索，跨会话记住用户偏好。

核心设计:
  三步：①extract_facts 用 PydanticAI Agent 从用户消息抽取值得长期记住的事实（套餐/手机号/昵称/偏好）；
  ②save_memory 把事实向量化存入 Milvus user_memories collection，按 tenant+user+fact_type 先删后插实现 upsert；
  ③search_memory 用当前 query 向量检索相关记忆，相似度 >= 阈值才返回。
  全程降级设计：抽取/保存/检索任一失败都返回空/不抛，记忆是"锦上添花"不能拖垮主对话。

关键技术点:
  - PydanticAI 结构化输出: output_type=list[Fact]，LLM 返回自动校验为 Fact 列表，无需手动 JSON 解析
  - upsert 语义: 按 tenant+user+fact_type 先 delete 再 insert，实现"套餐升级覆盖旧值"
  - 相似度阈值 0.35: 从 0.5 降下来，因为记忆 fact 内容短、表述差异大（用户说"我是高级套餐"vs存"plan=pro"），
    0.5 太严会漏召回关键偏好，0.35 提升召回率；记忆场景宁可多召回几条交 LLM 判断，也不要漏掉用户偏好
  - 降级策略: 失败返回空列表/不抛异常，记忆缺失不影响主对话

依赖关系:
  - 上游: app/services/agent_service.py（对话后 extract_facts+save_memory 抽取记忆，对话前 search_memory 注入记忆）
  - 下游: app/core/embedding_client.py（embed + Milvus memory collection）、pydantic_ai（结构化抽取）
"""

import logging
import re
import time

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)

MEMORY_TOP_K = 3       # 检索记忆条数
MEMORY_THRESHOLD = 0.35  # 相似度阈值，低于此值的记忆不注入
# 记忆事实类型白名单（T15）: fact_type 由 LLM 产出（不可信），
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


# ─── PydanticAI 结构化输出模型 ───
class Fact(BaseModel):
    """记忆事实结构（PydanticAI 保证 LLM 返回符合此结构）。

    fact_type: 事实类型（plan/phone/nickname/preference 等），用于 upsert 时按类型覆盖
    content: 事实内容
    """
    fact_type: str
    content: str


# ─── PydanticAI Agent（模块级单例）───
# output_type=list[Fact] 是核心：PydanticAI 在 LLM 返回后自动校验为 Fact 列表，
# 类型安全，无需手动剥围栏/解析 JSON/处理格式异常。这是 011 从 litellm+围栏剥离迁移过来的。
_extract_agent: Agent[None, list[Fact]] = Agent(
    model="deepseek:deepseek-chat",
    output_type=list[Fact],
    system_prompt=(
        "你是记忆抽取器。从用户消息中抽取值得长期记住的持久事实，"
        "如：套餐（plan）、手机号（phone）、用户称呼/昵称（nickname）、"
        "回答偏好（preference）等。\n"
        "没有可记住的事实则返回空列表。"
    ),
)


def extract_facts(user_message: str | None) -> list[dict]:
    """LLM 抽取关键用户事实 -> [{fact_type, content}]，无则空。

    流程:
      1. 空消息 -> 返回空
      2. PydanticAI Agent run_sync 抽取（output_type=list[Fact] 自动校验）
      3. 转换为 dict 列表，过滤掉 content 为空的
      4. 失败降级返回空列表

    设计决策:
      用 PydanticAI 而非裸 litellm + 手动 JSON 解析，是因为 LLM 返回格式不稳定
      （可能带 ```json 围栏、可能多返/少返字段），手动解析需大量防御代码且易错。
      output_type=list[Fact] 让 PydanticAI 在 SDK 层做校验和重试，类型安全。
      run_sync 是同步调用（项目整体同步架构，见 agent_service 说明）。

    使用 PydanticAI Agent（output_type=list[Fact]），类型安全，无需手动 JSON 解析。
    失败降级返回空列表。
    """
    if not user_message:
        return []

    try:
        # 013: async 端点内调用会触发 "This event loop is already running"（PydanticAI run_sync
        # 内部 asyncio.run），导致长期记忆抽取持续降级；改用线程兼容调用。
        from app.utils.asyncio_compat import run_sync_in_thread

        result = run_sync_in_thread(_extract_agent.run_sync, user_message)
        return [
            {"fact_type": f.fact_type, "content": f.content}
            for f in result.output
            if f.content
        ]
    except Exception as e:
        # 降级：抽取失败不影响主对话，返回空
        logger.warning(f"extract_facts failed (degraded): {e}")
        return []


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
    # M10: user_id 不安全（含引号/or 等可注入 Milvus 表达式）-> 跳过写入
    if not _is_safe_user_id(user_id):
        logger.warning(f"Memory save skipped: unsafe user_id {user_id!r}")
        return

    try:
        from app.core.embedding_client import (
            embed_texts,
            get_memory_collection,
            init_memory_collection,
        )

        init_memory_collection()
        collection = get_memory_collection()

        for fact in facts:
            # 白名单校验（T15）: fact_type 来自 LLM 输出，未校验直接拼进 Milvus 删除
            # 表达式可被诱导注入引号/or 扩大删除范围（跨租户删记忆）；白名单外跳过不落库
            if fact.get("fact_type") not in ALLOWED_FACT_TYPES:
                logger.warning(f"Memory fact_type not in whitelist, skipped: {fact.get('fact_type')!r}")
                continue
            # P1-新1: 记忆内容（可能含手机号）入库前脱敏
            from app.utils.desensitize import mask_pii
            content = mask_pii(fact.get("content"))
            if not content:
                continue
            # upsert：先删同 tenant+user+fact_type 的旧记忆，再插新的
            expr = (
                f'tenant_id == {tenant_id} and user_id == "{user_id}" '
                f'and fact_type == "{fact["fact_type"]}"'
            )
            try:
                collection.delete(expr)
            except Exception:
                # 删旧失败不阻塞：最坏情况是同类型记忆并存两条，检索时都能召回
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
        # flush 把缓冲区数据落盘，保证后续检索能查到
        collection.flush()
        logger.info(f"Saved {len(facts)} memories for user {user_id}")
    except Exception as e:
        # 降级：保存失败不抛，记忆缺失不影响主对话
        logger.warning(f"save_memory failed (degraded): {e}")


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
    # M10: user_id 不安全（含引号/or 等可注入 Milvus 表达式）-> 跳过检索
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

        memories = []
        for hit in results[0]:
            # 相似度阈值过滤：低于 0.35 的记忆不注入（避免无关记忆干扰回答）
            if hit.score >= threshold:
                content = hit.entity.get("content", "")
                if content:
                    memories.append(content)
        return memories
    except Exception as e:
        # 降级：检索失败返回空，主对话无记忆注入也能继续
        logger.warning(f"search_memory failed (degraded): {e}")
        return []
