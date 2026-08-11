"""app/utils/asyncio_compat.py

职责: 在事件循环线程中安全执行"内部使用 asyncio.run 的同步函数"
      （典型场景：PydanticAI Agent.run_sync 在 async 端点 /session/message/stream
      经 LangGraph graph.invoke 同步调用时，asyncio.run 抛
      "This event loop is already running" → 功能静默降级）。

修复背景（013 UI 联调实证）:
  - Router 意图识别在流式对话中持续降级（confidence=0.3 → 倾向转人工）
  - 长期记忆抽取持续降级（extract_facts degraded）
  均因 run_sync 在已有事件循环线程内调用 asyncio.run。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable


def run_sync_in_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """调用 func(*args, **kwargs)；若当前线程已有运行中的事件循环，则在独立线程执行。

    无运行中事件循环（同步上下文，如非流式 /message 端点）→ 直接调用，零开销。
    有运行中事件循环（async 端点内同步调用）→ 独立线程执行并等待结果，
    避免 func 内部 asyncio.run 抛 "This event loop is already running"。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return func(*args, **kwargs)

    holder: dict[str, Any] = {}

    def _worker() -> None:
        holder["result"] = func(*args, **kwargs)

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    return holder["result"]
