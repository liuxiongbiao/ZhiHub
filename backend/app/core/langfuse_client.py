"""
backend/app/core/langfuse_client.py

职责: Langfuse 可观测 SDK 的封装--单例懒加载 + 降级安全的 observe 装饰器。

核心设计:
  可观测是"锦上添花"而非主流程，必须满足"Langfuse 挂了对话照常跑"。因此本文件的核心是降级：
  未配置 key -> get_langfuse 返回 None -> observe 返回 no-op 装饰器（原样执行函数，不追踪）。
  不直接用官方 @observe 挂在业务函数上，而是自封装一层 observe()，原因是官方装饰器在模块加载时
  就会尝试初始化 client，失败可能抛异常；自封装把初始化推迟到运行时并 try/except 兜底，确保零侵入。
  另外 capture_input/output=False，不把原始用户消息（含手机号 PII）传给 Langfuse，PII 明细只存
  脱敏后的 MongoDB trace（见 trace_service.py），这是 008 US3 脱敏要求的落实。

关键技术点:
  - 单例懒加载: _langfuse_instance 首次调用 get_langfuse 时初始化，后续复用
  - 降级 no-op: 未配置/初始化失败/observe 不可用，全部回退到原样执行的空装饰器
  - PII 安全: capture_input=False/capture_output=False，Langfuse 只存 trace 元数据不存原始 I/O
  - env 同步: 把 settings 同步到 LANGFUSE_* env，兼容 SDK 的 module-level observe

依赖关系:
  - 上游: app/services/agent_service.py / trace_service.py（用 @observe 装饰追踪、flush 退出前上报）
  - 下游: app/config.py（读 langfuse_* 配置）、langfuse SDK（pip 包，可选依赖）
"""

import logging
import os
from functools import wraps

from app.config import settings

logger = logging.getLogger(__name__)

# 单例缓存；None 表示"尚未尝试初始化"，初始化后存 Langfuse 实例（或失败后保持 None 走降级）
_langfuse_instance = None


def _is_configured() -> bool:
    """是否配置了 Langfuse 凭证（public + secret key 都非空才算配置）。"""
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def get_langfuse():
    """返回 Langfuse 单例；未配置或初始化失败返回 None（调用方据此走降级）。

    流程:
      1. 已有单例 -> 直接返回
      2. 未配置 key -> 返回 None（降级 no-op）
      3. 首次初始化：同步 env -> import SDK -> 构造 Langfuse -> 缓存并返回
      4. 任何异常 -> 记告警返回 None（降级，主流程不受影响）

    设计决策:
      SDK import 放在函数内而非模块顶部，是"可选依赖"模式--没有装 langfuse 包或未配置时，
      模块本身仍能正常 import，只在真正要追踪时才尝试加载 SDK，失败则降级。这样可观测模块的
      缺失不会影响整个应用启动。
    """
    global _langfuse_instance
    if _langfuse_instance is not None:
        return _langfuse_instance
    if not _is_configured():
        # 未配置：直接降级，不尝试初始化
        return None
    try:
        # 同步 env 给 langfuse SDK 默认 client（module-level observe 依赖 LANGFUSE_* env）
        os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_base_url
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        from langfuse import Langfuse

        _langfuse_instance = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
        )
        logger.info("Langfuse client initialized")
        return _langfuse_instance
    except Exception as e:
        # 初始化失败：降级 no-op，绝不向上抛（可观测故障不能拖垮主流程）
        logger.warning(f"Langfuse init failed (degraded, no-op): {e}")
        return None


def _noop_decorator():
    """无 Langfuse 时的 no-op 装饰器：原样执行被装饰函数，零开销零追踪。"""

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    return deco


def observe(name: str | None = None):
    """装饰器工厂：有 Langfuse 时用 langfuse.observe 追踪，无时 no-op 不抛。

    流程:
      1. get_langfuse() 为 None -> 返回 no-op 装饰器
      2. 有实例 -> 返回官方 observe 装饰器，但关闭 input/output 采集
      3. observe 本身异常 -> 降级 no-op

    设计决策:
      capture_input=False/capture_output=False 是 PII 安全核心--用户消息里可能有手机号，
      传到 Langfuse cloud 就泄露了。Langfuse 只用来追踪调用层级和耗时（trace 树），
      原始 I/O 的脱敏明细存本地 MongoDB trace（trace_service.mask_pii 处理后再写）。

    用法:
        @observe(name="agent.handle_message")
        def handle_message(...): ...
    """
    if get_langfuse() is None:
        return _noop_decorator()
    try:
        from langfuse import observe as _lf_observe

        # PII 安全：不采集原始 input/output（手机号可能出现在用户消息中）
        return _lf_observe(name=name, capture_input=False, capture_output=False)
    except Exception as e:
        logger.warning(f"langfuse observe unavailable, no-op: {e}")
        return _noop_decorator()


def flush() -> None:
    """同步 flush trace 上报（可选，进程退出前调用保证不丢）。

    设计决策:
      Langfuse SDK 默认异步批量上报，进程异常退出可能丢未 flush 的 trace。应用关闭时调用本函数
      确保追踪数据落盘。失败也只告警不抛--退出阶段不应因可观测报错卡住。
    """
    lf = get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception as e:
            logger.warning(f"langfuse flush failed: {e}")
