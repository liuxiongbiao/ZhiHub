"""
backend/app/core/redis.py

职责: Redis 连接池单例 + 登录失败计数状态机 + 令牌黑名单辅助
分工: 🔴 强模型独立完成（失败计数原子性、黑名单 TTL 写错 = 安全防线失效）
对应日程: Day 2 / 001-account-tenant（失败计数/黑名单，research R1/R4）
         Day 6 将在此基础补充分布式锁

核心设计:
  三个职责共用一个 Redis 连接（进程级单例，decode_responses=True 返回 str）。
  登录失败计数用 INCR 原子自增 + 分阶段 TTL 实现状态机锁定。
  令牌黑名单用 SET key EX ttl，key 自然过期 = 零维护。

键设计（见 data-model.md）:
  login_fail:{phone}      String(int)  连续失败次数；达阈值时设 15min 锁定 TTL
  token:blocklist:{jti}   String       登出黑名单；TTL = 令牌剩余有效期

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
连接管理
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  get_redis() -> 进程级单例，decode_responses=True（返回 str 而非 bytes）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
登录失败计数状态机（login_fail:{phone}）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  密码错误 -> incr_login_fail(phone)
    ├─ 首次 -> INCR + EXPIRE（滚动窗口 TTL）
    ├─ 2-4次 -> INCR（不重置 TTL）
    └─ ≥5次 -> INCR + EXPIRE（重设 TTL = 15分钟锁定时长）
  判定锁定 -> get_lock_ttl(phone) > 0
  密码正确 -> clear_login_fail(phone) -> DEL key

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
令牌黑名单（token:blocklist:{jti}）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  登出 -> blocklist_token(jti, ttl=剩余有效期) -> SET key EX ttl
  鉴权 -> is_blocklisted(jti) -> EXISTS key
  key 自然过期后自动删除，零维护

关键技术点:
  - 登录失败状态机（INCR + EXPIRE 组合）:
      首次失败:  INCR(1) + EXPIRE(15min)        设滚动窗口，key 15min 内无新失败则自动归零
      2-4 次失败: INCR（不重置 TTL）            继续在原窗口内累计
      ≥5 次失败: INCR + EXPIRE(15min)           重设 TTL 进入锁定，此后 15min 内即使密码正确也拒绝
    为什么用 INCR 而不是 GET+SET: INCR 是原子操作，并发请求不会丢计数；
    GET+SET 在并发下会丢更新（两个请求同时读到 count=3，各自写 4，实际该是 5）。
  - 锁定判定 get_lock_ttl: 只在 count≥5 时查 TTL，避免每次登录都查 TTL 的开销；
    阈值以下直接返回 0（未锁定）。
  - 令牌黑名单 SETEX: 登出时将 jti 写入 Redis，TTL = JWT 剩余有效期。
    JWT 无状态无法主动失效，黑名单是唯一吊销手段；
    TTL 到期 key 自动删除，无需清理任务，也保证黑名单不会无限膨胀。
  - 连接单例: 进程级 _redis_client，避免每次请求新建 TCP 连接的开销。

依赖关系:
  - 上游: app/api/auth.py（登录失败计数）、app/core/auth_middleware（黑名单校验）、
          app/api/auth.py logout（黑名单写入）
  - 下游: Redis 服务（通过 redis-py 客户端）
"""

import time

import redis

from app.config import settings

# 登录失败：滚动统计窗口与锁定时长
LOGIN_FAIL_WINDOW_SECONDS = 15 * 60  # 未达阈值时失败计数的存活窗口（15min，超时自动归零）
LOCK_SECONDS = 15 * 60              # 达阈值后的锁定时长（PRD：15 分钟）
MAX_FAIL_ATTEMPTS = 5              # PRD：连续 5 次失败即锁定

# 进程级 Redis 客户端单例（模块全局变量，首次调用 get_redis 时初始化）
_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """返回进程级 Redis 客户端单例。

    decode_responses=True 使所有返回值为 str 而非 bytes，
    省去调用方每次 decode 的样板代码（如 r.get() 直接返回 str）。

    Returns:
        redis.Redis 客户端实例（单例）
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
        )
    return _redis_client


def _login_fail_key(phone: str) -> str:
    """构造登录失败计数的 Redis key。"""
    return f"login_fail:{phone}"


def _login_fail_ip_key(ip: str) -> str:
    """构造 IP 维度登录失败计数的 Redis key（T16 防刷锁他人账号）。

    攻击者对一个已知手机号故意输错 5 次可锁定对方账号 15min（账号 DoS）；
    IP 维度让攻击者 5 次失败先锁住自己的 IP。
    """
    return f"login_fail_ip:{ip}"


def _blocklist_key(jti: str) -> str:
    """构造令牌黑名单的 Redis key。"""
    return f"token:blocklist:{jti}"


# ---------- 登录失败计数与锁定 ----------
def incr_login_fail(phone: str) -> int:
    """失败次数 +1（原子 INCR），返回当前累计次数，并按状态机阶段设置 TTL。

    状态机:
      count == 1（首次）: 设滚动窗口 TTL（15min），超时未再失败则自动归零
      count >= 5（达阈值）: 重设 TTL 为锁定时长（15min），进入锁定
      其他（2-4 次）: 仅自增，不重置 TTL（继续在首次设的窗口内累计）

    为什么 INCR + EXPIRE 分两步而非用 Lua: 两步在极端情况下有微小竞态
    （INCR 成功但 EXPIRE 前进程崩溃），但 demo 阶段可接受；
    生产环境可用 Lua 脚本保证原子性。

    Args:
        phone: 用户手机号

    Returns:
        当前失败次数（int）
    """
    return _incr_login_fail_key(_login_fail_key(phone))


def _incr_login_fail_key(key: str) -> int:
    """失败计数自增 + 按状态机阶段设置 TTL（phone 与 IP 维度共用，T16）。"""
    r = get_redis()
    count = r.incr(key)
    if count == 1:
        # 首次失败：设滚动窗口，15min 内无新失败则 key 自动过期归零
        r.expire(key, LOGIN_FAIL_WINDOW_SECONDS)
    if count >= MAX_FAIL_ATTEMPTS:
        # 达阈值：重设 TTL 进入锁定（覆盖之前的滚动窗口 TTL）
        r.expire(key, LOCK_SECONDS)
    return count


def incr_login_fail_ip(ip: str) -> int:
    """IP 维度失败次数 +1（T16），与 incr_login_fail 同阈值/同锁定时长。"""
    return _incr_login_fail_key(_login_fail_ip_key(ip))


def get_login_fail(phone: str) -> int:
    """获取当前失败次数，key 不存在返回 0。"""
    val = get_redis().get(_login_fail_key(phone))
    return int(val) if val is not None else 0


def get_lock_ttl(phone: str) -> int:
    """返回剩余锁定秒数；未锁定（未达阈值或 key 不存在）返回 0。

    先判 count < MAX_FAIL_ATTEMPTS 直接返回 0，避免阈值以下时多查一次 TTL。
    只有 count >= 5 时才查 TTL 确认锁定剩余时间。

    Args:
        phone: 用户手机号

    Returns:
        剩余锁定秒数（>0 表示已锁定），0 表示未锁定
    """
    r = get_redis()
    key = _login_fail_key(phone)
    if get_login_fail(phone) < MAX_FAIL_ATTEMPTS:
        return 0
    ttl = r.ttl(key)
    return ttl if ttl and ttl > 0 else 0


def is_locked(phone: str) -> bool:
    """是否已锁定（剩余锁定时间 > 0）。"""
    return get_lock_ttl(phone) > 0


def clear_login_fail(phone: str) -> None:
    """登录成功后清零失败计数（DEL key）。

    必须在密码校验通过后调用，否则已累计的计数会残留。
    """
    get_redis().delete(_login_fail_key(phone))


def get_lock_ttl_ip(ip: str) -> int:
    """IP 维度剩余锁定秒数（0 表示未锁定，T16）。"""
    r = get_redis()
    key = _login_fail_ip_key(ip)
    val = r.get(key)
    if val is None or int(val) < MAX_FAIL_ATTEMPTS:
        return 0
    ttl = r.ttl(key)
    return ttl if ttl and ttl > 0 else 0


def clear_login_fail_ip(ip: str) -> None:
    """IP 维度失败计数清零（登录成功时，T16）。"""
    get_redis().delete(_login_fail_ip_key(ip))


# ---------- 登录 IP 频率限制（T16） ----------
IP_RATE_LIMIT_PER_MINUTE = 30  # 登录端点每 IP 每分钟最多尝试次数


def check_ip_rate_limit(ip: str, limit: int = IP_RATE_LIMIT_PER_MINUTE) -> bool:
    """登录端点 IP 频率限制：每分钟 limit 次，超限返回 False（调用方 429）。

    固定窗口实现: 窗口键（含分钟分片）INCR + 首请求设 60s TTL。
    用分钟分片避免固定窗口的"跨分钟边界清零"误判（最多漏 1 分钟窗口的计数）。

    Args:
        ip: 客户端 IP
        limit: 每分钟允许次数

    Returns:
        True = 放行；False = 超限
    """
    r = get_redis()
    minute = int(time.time()) // 60
    key = f"login_rate:{ip}:{minute}"
    count = r.incr(key)
    if count == 1:
        r.expire(key, 120)  # 覆盖当前分钟窗口 + 缓冲
    return count <= limit


# ---------- 令牌黑名单 ----------
def blocklist_token(jti: str, ttl: int) -> None:
    """将令牌 jti 加入黑名单，存活 ttl 秒（=令牌剩余有效期）。

    登出时调用: 从 JWT payload 取 exp 计算剩余有效期作为 TTL，
    写入 Redis 后该 jti 在 TTL 内被鉴权中间件拒绝。
    TTL 到期 key 自动删除，黑名单不会无限膨胀。

    ttl <= 0 表示令牌已过期（无需加入黑名单，过期令牌本身就会被拒绝）。

    Args:
        jti: JWT ID（令牌唯一标识）
        ttl: 黑名单存活秒数（=令牌剩余有效期）
    """
    if ttl <= 0:
        return
    get_redis().set(_blocklist_key(jti), "1", ex=ttl)


def is_blocklisted(jti: str) -> bool:
    """检查 jti 是否在黑名单中（EXISTS key）。

    鉴权中间件调用: decode_access_token 通过后，额外检查 jti 是否已吊销。
    黑名单 key 有 TTL，过期自动删除，EXISTS 返回 0。

    Args:
        jti: JWT ID

    Returns:
        True 已吊销（拒绝访问），False 未吊销
    """
    return get_redis().exists(_blocklist_key(jti)) == 1
