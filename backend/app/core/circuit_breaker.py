"""
backend/app/core/circuit_breaker.py

职责: MCP 工具调用的熔断状态机，保护后端在工具持续故障时不被拖垮。

核心设计:
  经典三态熔断：closed（正常放行）-> open（熔断，直接返回兜底，不调工具）-> half_open（放一个
  探测请求试探恢复）-> closed（恢复）。状态不存内存而存 Redis，原因是后端可能多进程/多实例部署，
  内存态各实例独立会导致熔断不一致；Redis 集中存储让所有实例共享同一份熔断状态。
  调用方（mcp_service.call_tool）在调工具前先 is_broken 检查，调完根据结果 record_success/record_failure。

关键技术点:
  - 三态机: closed -> open（连续 5 次失败）/ open -> half_open（30s 超时）/ half_open -> closed（3 次成功）
  - Redis 存储: key=mcp:circuit:{tool}，value=JSON 状态，TTL 1 小时防永久残留
  - half_open 探测: open 超时后放一个请求，成功累计、失败立即回 open 重置计时
  - 按 tool_name 隔离: 每个工具独立熔断，一个工具挂了不影响其他工具

依赖关系:
  - 上游: app/services/mcp_service.py（is_broken 判断是否短路、record_success/failure 上报结果）
  - 下游: app/core/redis.py（get_redis 连接池）
"""

import json
import logging
import time

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

CIRCUIT_KEY_PREFIX = "mcp:circuit:"
FAILURE_THRESHOLD = 5      # closed 态连续失败次数达此值 -> 熔断转 open
RECOVERY_THRESHOLD = 3     # half_open 态连续成功次数达此值 -> 恢复转 closed
HALF_OPEN_TIMEOUT = 30     # open 状态持续多少秒后转 half_open 放探测请求（秒）
CIRCUIT_TTL = 3600         # 状态 key TTL（1 小时），防止异常情况下状态永久残留

STATE_CLOSED = "closed"      # 正常态：放行所有请求，累计失败计数
STATE_OPEN = "open"          # 熔断态：拦截所有请求，直接走兜底
STATE_HALF_OPEN = "half_open"  # 半开态：放行探测请求，累计成功计数


def _key(tool_name: str) -> str:
    """拼装工具对应的 Redis key。按工具名隔离，互不影响。"""
    return f"{CIRCUIT_KEY_PREFIX}{tool_name}"


def get_state(tool_name: str) -> dict:
    """读取工具当前熔断状态。无记录则返回 closed 初始态。

    设计决策:
      状态损坏（JSON 解析失败）时直接重置为 closed 初始态而非抛异常--熔断是保护机制，
      状态丢了最坏情况是"该熔断时没熔断"，但绝不能让熔断自身的故障把工具调用链打断。
      宁可降级到放行，也不能因熔断模块故障导致工具永久不可用。

    Returns:
        {"state","fail_count","recover_count","last_failed_at","opened_at"} 状态字典
    """
    r = get_redis()
    data = r.get(_key(tool_name))
    if data:
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            # 状态 JSON 损坏：记录告警并重置，宁可放行也不卡死调用链
            logger.warning(f"Circuit breaker state for {tool_name} corrupted, resetting")
    # 无记录或损坏：返回 closed 初始态
    return {
        "state": STATE_CLOSED,
        "fail_count": 0,
        "recover_count": 0,
        "last_failed_at": None,
        "opened_at": None,
    }


def _save(tool_name: str, state: dict) -> None:
    """持久化状态到 Redis，带 TTL 防永久残留。"""
    r = get_redis()
    r.set(_key(tool_name), json.dumps(state), ex=CIRCUIT_TTL)


def is_broken(tool_name: str) -> bool:
    """检查工具是否处于熔断中（调用方据此决定是否短路返回兜底）。

    流程:
      1. 读当前状态
      2. open 态：若已超过 HALF_OPEN_TIMEOUT，转 half_open 放一个探测请求（返回 False 放行）
         否则继续拦截（返回 True）
      3. closed / half_open 态：放行（返回 False）

    设计决策:
      "open 超时自动转 half_open"放在 is_broken 里而不是后台定时任务，原因是无状态触发更简单--
      下次有人调这个工具时顺手检查超时并转换，不需要额外的定时线程。代价是：没人调就一直停在 open，
      但这恰恰是期望行为（没流量就没必要试探恢复）。

    Args:
        tool_name: 工具名
    Returns:
        True 表示当前应短路返回兜底（不调工具）；False 表示放行调用
    """
    s = get_state(tool_name)
    if s["state"] == STATE_OPEN:
        opened_at = s.get("opened_at")
        if opened_at and time.time() - opened_at > HALF_OPEN_TIMEOUT:
            # open 已超时：SETNX 抢探测锁，仅一个请求转 half_open（防并发多探测）
            r = get_redis()
            probe_key = _key(tool_name) + ":probe"
            if r.set(probe_key, "1", nx=True, ex=HALF_OPEN_TIMEOUT):
                s["state"] = STATE_HALF_OPEN
                s["recover_count"] = 0
                _save(tool_name, s)
                logger.info(f"Circuit {tool_name}: open -> half_open (probe allowed)")
                return False
            # 未抢到锁：已有探测在进行，继续拦截
            return True
        # 仍在熔断窗口内：拦截
        return True
    # closed / half_open 都不拦截（half_open 放探测请求累计成功）
    return False


def record_failure(tool_name: str) -> None:
    """记录一次工具调用失败（关键字段空/异常/超时等）。

    流程:
      1. half_open 态失败 -> 立即回 open，重置计时和计数（探测失败说明没恢复）
      2. closed 态：fail_count+1，达到 FAILURE_THRESHOLD 则转 open
      3. open 态：理论上不会进来（被 is_broken 拦截），忽略

    设计决策:
      half_open 失败立即回 open 而非继续累计，是因为 half_open 本就是"试探"--一个失败就足以说明
      工具还没恢复，没必要继续放流量。回到 open 重新等 30s 超时，给后端更长的恢复窗口。
    """
    s = get_state(tool_name)
    if s["state"] == STATE_HALF_OPEN:
        # 半开态失败 -> 立即回 open，重置计时，重新等超时
        s["state"] = STATE_OPEN
        s["opened_at"] = time.time()
        s["fail_count"] = 0
        s["recover_count"] = 0
        _save(tool_name, s)
        get_redis().delete(_key(tool_name) + ":probe")  # 释放探测锁，下次超时新探测
        logger.info(f"Circuit {tool_name}: half_open -> open (probe failed)")
        return
    # closed 态累计失败
    s["fail_count"] = s.get("fail_count", 0) + 1
    s["last_failed_at"] = time.time()
    if s["fail_count"] >= FAILURE_THRESHOLD:
        # 达阈值：熔断
        s["state"] = STATE_OPEN
        s["opened_at"] = time.time()
        s["fail_count"] = 0
        logger.info(f"Circuit {tool_name}: closed -> open (failures reached threshold)")
    _save(tool_name, s)


def record_success(tool_name: str) -> None:
    """记录一次工具调用成功。

    流程:
      1. half_open 态：recover_count+1，达 RECOVERY_THRESHOLD 则转 closed（恢复）
      2. closed 态：清零失败计数（连续失败被打断，重新累计）
      3. open 态：不应有成功（被 is_broken 拦截），忽略

    设计决策:
      closed 态成功要清零 fail_count--熔断看的是"连续"失败，中间有一次成功就说明工具恢复了，
      不能让历史失败累计触发误熔断。这是"连续失败"语义的正确实现。
    """
    s = get_state(tool_name)
    if s["state"] == STATE_HALF_OPEN:
        s["recover_count"] = s.get("recover_count", 0) + 1
        if s["recover_count"] >= RECOVERY_THRESHOLD:
            # 探测连续成功达阈值：恢复
            s["state"] = STATE_CLOSED
            s["fail_count"] = 0
            s["recover_count"] = 0
            logger.info(f"Circuit {tool_name}: half_open -> closed (recovered)")
        _save(tool_name, s)
        if s["state"] == STATE_CLOSED:
            get_redis().delete(_key(tool_name) + ":probe")  # 恢复则释放探测锁
    elif s["state"] == STATE_CLOSED:
        # 正常态成功：清零失败计数（连续失败被打断）
        if s.get("fail_count", 0) > 0:
            s["fail_count"] = 0
            _save(tool_name, s)
    # open 态不应有成功（被 is_broken 拦截），忽略
