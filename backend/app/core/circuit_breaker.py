"""
backend/app/core/circuit_breaker.py

职责: MCP 工具调用的熔断状态机（ 加固版）

设计:
  - 三态: closed -> open -> half_open -> closed
  - 状态存 Redis（多实例共享）；所有读改写通过短 TTL 分布式锁串行化，
    避免并发请求互相覆盖计数（原实现 read-modify-write 非原子）。
  - half_open 严格单探测: 探测权用独立 key（SET NX）抢占，同一时刻只放行
    一个探测请求；探测成功未达恢复阈值时释放探测权放行下一个串行探测。
"""

import json
import logging
import secrets
import time

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

CIRCUIT_KEY_PREFIX = "mcp:circuit:"
FAILURE_THRESHOLD = 5      # closed 态连续失败次数达此值 -> open
RECOVERY_THRESHOLD = 3     # half_open 态连续成功次数达此值 -> closed
HALF_OPEN_TIMEOUT = 30     # open 持续秒数后允许转 half_open
CIRCUIT_TTL = 3600         # 状态 key TTL，防止状态永久残留
MUTATION_LOCK_TTL = 10     # 状态写锁 TTL（秒）
MUTATION_LOCK_RETRIES = 10
MUTATION_LOCK_RETRY_DELAY = 0.05

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


def _key(tool_name: str) -> str:
    return f"{CIRCUIT_KEY_PREFIX}{tool_name}"


def _lock_key(tool_name: str) -> str:
    return _key(tool_name) + ":lock"


def _probe_key(tool_name: str) -> str:
    return _key(tool_name) + ":probe"


def _default_state() -> dict:
    return {
        "state": STATE_CLOSED,
        "fail_count": 0,
        "recover_count": 0,
        "last_failed_at": None,
        "opened_at": None,
    }


def get_state(tool_name: str) -> dict:
    """读取当前熔断状态；无记录/损坏时重置为 closed。"""
    r = get_redis()
    data = r.get(_key(tool_name))
    if data:
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Circuit breaker state for {tool_name} corrupted, resetting")
    return _default_state()


def _save(tool_name: str, state: dict) -> None:
    get_redis().set(_key(tool_name), json.dumps(state), ex=CIRCUIT_TTL)


def _with_mutation_lock(tool_name: str, fn) -> None:
    """串行化状态读改写。抢锁失败时降级直接执行（低频工具调用，可接受）。"""
    r = get_redis()
    token = secrets.token_hex(8)
    for _ in range(MUTATION_LOCK_RETRIES):
        if r.set(_lock_key(tool_name), token, nx=True, ex=MUTATION_LOCK_TTL):
            try:
                fn()
            finally:
                try:
                    r.delete(_lock_key(tool_name))
                except Exception:
                    pass
            return
        time.sleep(MUTATION_LOCK_RETRY_DELAY)
    logger.warning(f"Circuit {tool_name}: mutation lock not acquired, applying without lock")
    fn()


def is_broken(tool_name: str) -> bool:
    """True=短路（不调工具）；False=放行。half_open 只放行唯一探测请求。"""
    s = get_state(tool_name)
    now = time.time()
    r = get_redis()

    if s["state"] == STATE_CLOSED:
        return False

    if s["state"] == STATE_OPEN:
        opened_at = s.get("opened_at")
        if opened_at and now - opened_at > HALF_OPEN_TIMEOUT:
            # open 超时：抢占探测权 → 转 half_open → 放行唯一探测
            if r.set(_probe_key(tool_name), "1", nx=True, ex=HALF_OPEN_TIMEOUT):
                s["state"] = STATE_HALF_OPEN
                s["recover_count"] = 0
                s["probed_at"] = now
                _save(tool_name, s)
                logger.info(f"Circuit {tool_name}: open -> half_open (single probe allowed)")
                return False
        return True

    # half_open：仅持有探测权者放行（SET NX 保证单探测）
    return not r.set(_probe_key(tool_name), "1", nx=True, ex=HALF_OPEN_TIMEOUT)


def record_failure(tool_name: str) -> None:
    """记录一次失败：half_open 失败立即回 open；closed 连续失败达阈值转 open。"""
    def _apply():
        s = get_state(tool_name)
        r = get_redis()
        if s["state"] == STATE_HALF_OPEN:
            s["state"] = STATE_OPEN
            s["opened_at"] = time.time()
            s["fail_count"] = 0
            s["recover_count"] = 0
            _save(tool_name, s)
            r.delete(_probe_key(tool_name))
            logger.info(f"Circuit {tool_name}: half_open -> open (probe failed)")
            return
        if s["state"] == STATE_CLOSED:
            s["fail_count"] = s.get("fail_count", 0) + 1
            s["last_failed_at"] = time.time()
            if s["fail_count"] >= FAILURE_THRESHOLD:
                s["state"] = STATE_OPEN
                s["opened_at"] = time.time()
                s["fail_count"] = 0
                logger.info(f"Circuit {tool_name}: closed -> open (failures reached threshold)")
            _save(tool_name, s)

    _with_mutation_lock(tool_name, _apply)


def record_success(tool_name: str) -> None:
    """记录一次成功：half_open 连续成功达阈值恢复 closed；closed 清零失败计数。"""
    def _apply():
        s = get_state(tool_name)
        r = get_redis()
        if s["state"] == STATE_HALF_OPEN:
            s["recover_count"] = s.get("recover_count", 0) + 1
            r.delete(_probe_key(tool_name))  # 释放探测权，允许下一个串行探测
            if s["recover_count"] >= RECOVERY_THRESHOLD:
                s["state"] = STATE_CLOSED
                s["fail_count"] = 0
                s["recover_count"] = 0
                logger.info(f"Circuit {tool_name}: half_open -> closed (recovered)")
            _save(tool_name, s)
            return
        if s["state"] == STATE_CLOSED:
            if s.get("fail_count", 0) > 0:
                s["fail_count"] = 0
                _save(tool_name, s)

    _with_mutation_lock(tool_name, _apply)
