"""
backend/app/core/llm_client.py

职责: 模型网关封装 -- 主模型失败自动切备用模型（PRD 3.14）

核心设计:
  llm_complete(messages, **kwargs) 是 litellm.completion 的薄封装，增加主备降级能力。
  主模型（llm_primary_model）调用失败时 try/except 切备用模型（llm_backup_model），
  切换记 logger warning。主备都失败则抛出最后异常，由调用方走自己的兜底逻辑。

  为什么不用 litellm 内置的 completion_with_fallbacks:
    1. 需要自定义切换日志格式（对接 Langfuse trace）
    2. fallbacks 参数需传入 model 列表，配置灵活性不如 settings
    3. 薄封装更易测试（mock litellm.completion 即可）

关键技术点:
  - 主备降级: try primary -> except -> try backup，一次切换不重试
  - 配置来源: settings.llm_primary_model / llm_backup_model，默认 deepseek/deepseek-chat
  - kwargs 透传: max_tokens / temperature 等参数原样传给 litellm.completion
  - 异常不吞: 主备都失败时抛出最后异常，调用方有 try/except 兜底（如 _generate_answer）

依赖关系:
  - 上游: knowledge_agent / retrieval_service / planner（替代 litellm.completion 直调）
  - 下游: litellm.completion、app.config.settings
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 默认模型（config 为空时使用）
_DEFAULT_MODEL = "deepseek/deepseek-chat"
# LLM 调用超时: litellm 默认 600s，同步 invoke 架构下挂起会阻塞整个请求线程；
# 60s 覆盖正常生成，超时快速失败走调用方兜底
LLM_TIMEOUT_SECONDS = 60


def _log_model_fallback(primary: str, backup: str) -> None:
    """PRD 3.14：模型切换事件写入 Langfuse（失败仅告警，不影响主流程）。"""
    try:
        from app.core.langfuse_client import get_langfuse

        lf = get_langfuse()
        if lf is not None:
            lf.log_event(
                name="model_fallback",
                input={"primary_model": primary},
                output={"backup_model": backup},
            )
    except Exception as e:
        logger.warning(f"Langfuse model fallback event failed: {e}")


def llm_complete(messages: list[dict], **kwargs) -> Any:
    """LLM 调用封装：主模型失败 try/except 切备用模型（PRD 3.14）。

    流程:
      1. 从 settings 读取主备模型（为空用默认 deepseek/deepseek-chat）
      2. litellm.completion 调主模型（统一 timeout，调用方可覆盖）
      3. 失败 -> logger.warning 记切换 -> litellm.completion 调备用模型
      4. 备用也失败 -> 抛出异常（调用方自行兜底）

    设计决策:
      - 一次切换不重试: 重试会增加延迟且可能打满配额，一次切备用已足够
      - 主备模型相同不切换: 相同模型重试无意义，直接抛异常；
        备用模型应在 config 配置不同模型（llm_backup_model）
      - 不吞异常: 调用方（如 _generate_answer）有自己的 try/except 兜底回复，
        llm_complete 只负责切换，不负责兜底文案
      - kwargs 透传: max_tokens/temperature 等参数原样透传，调用方按场景设定；
        timeout 特殊处理（默认 LLM_TIMEOUT_SECONDS，调用方可覆盖）

    Args:
        messages: OpenAI 格式消息列表 [{"role": ..., "content": ...}, ...]
        **kwargs: 透传给 litellm.completion 的参数（max_tokens, temperature, timeout 等）

    Returns:
        litellm ModelResponse 对象

    Raises:
        Exception: 主备模型都失败时，抛出最后一次调用的异常
    """
    import litellm

    from app.config import settings

    primary = settings.llm_primary_model or _DEFAULT_MODEL
    backup = settings.llm_backup_model or _DEFAULT_MODEL
    timeout = kwargs.pop("timeout", LLM_TIMEOUT_SECONDS)

    try:
        return litellm.completion(model=primary, messages=messages, timeout=timeout, **kwargs)
    except Exception as e:
        if backup == primary:
            # 主备相同: 重试同一模型无意义，直接抛（备用模型需在 config 配置不同模型）
            logger.warning(f"Primary model {primary} failed and backup is identical, no failover: {e}")
            raise
        logger.warning(
            f"Primary model {primary} failed: {e}, switching to backup {backup}"
        )
        result = litellm.completion(model=backup, messages=messages, timeout=timeout, **kwargs)
        _log_model_fallback(primary, backup)
        return result


def llm_complete_stream(messages: list[dict], **kwargs):
    """LLM 流式调用: yield 文本 chunk（token 级真流式）。

    与 llm_complete 的区别:
      - llm_complete 返回完整 ModelResponse（同步阻塞）
      - llm_complete_stream 是生成器，逐 chunk yield 文本片段

    主备降级: 主模型流式失败 -> 尝试备用模型流式；都失败抛异常。

    Args:
        messages: OpenAI 格式消息列表
        **kwargs: 透传给 litellm.completion（max_tokens, temperature 等）

    Yields:
        str: 文本 chunk（非完整响应，拼接后为完整回答）
    """
    import litellm
    from app.config import settings

    primary = settings.llm_primary_model or _DEFAULT_MODEL
    backup = settings.llm_backup_model or _DEFAULT_MODEL
    timeout = kwargs.pop("timeout", LLM_TIMEOUT_SECONDS)

    try:
        response = litellm.completion(
            model=primary, messages=messages, timeout=timeout, stream=True, **kwargs
        )
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    except Exception as e:
        if backup == primary:
            raise
        logger.warning(f"Stream primary {primary} failed: {e}, trying backup {backup}")
        response = litellm.completion(
            model=backup, messages=messages, timeout=timeout, stream=True, **kwargs
        )
        _log_model_fallback(primary, backup)
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
