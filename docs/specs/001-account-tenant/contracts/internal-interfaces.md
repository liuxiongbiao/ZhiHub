# Internal Interface Contract: 模块间接口契约

定义本 feature 各层的函数签名契约（分层边界，宪法原则 I）。实现 MUST 匹配签名；测试针对这些契约先行编写。

---

## core/database.py（🆕）

```python
engine                                      # SQLAlchemy Engine（同步，pymysql）
SessionLocal                                # sessionmaker
def get_db() -> Generator[Session]          # FastAPI 依赖，yield 一个 Session，finally close
```

## core/redis.py（🔴 补充黑名单/计数辅助）

```python
def get_redis() -> Redis                    # 连接池单例
# 登录失败计数
def incr_login_fail(phone: str) -> int      # INCR，返回当前次数；首次设滚动窗口 TTL
def get_login_fail(phone: str) -> int       # 当前失败次数（不存在=0）
def lock_account(phone: str, seconds: int)  # 达阈值时设锁定 TTL
def get_lock_ttl(phone: str) -> int         # 剩余锁定秒数（0=未锁）
def clear_login_fail(phone: str) -> None     # 登录成功清零 DEL
# 令牌黑名单
def blocklist_token(jti: str, ttl: int)     # SET token:blocklist:{jti} EX ttl
def is_blocklisted(jti: str) -> bool        # EXISTS
```

## utils/validators.py（🟢）

```python
def validate_phone(phone: str) -> None      # 不合法 raise ValueError
def validate_password_strength(pw: str) -> None  # 不合法 raise ValueError（含具体原因）
```

## crud/user.py（🟢，只存取，无业务判断）

```python
def get_by_phone(db: Session, phone: str) -> User | None
def create(db: Session, *, phone: str, password_hash: str, role_default: str = "admin") -> User
```

## crud/tenant.py（🟢）

```python
def create_tenant(db: Session, *, name: str) -> Tenant   # plan_type=trial, seat_limit=1, active
def add_member(db: Session, *, tenant_id: int, user_id: int, role: str) -> TenantMember
def get_by_id(db: Session, tenant_id: int) -> Tenant | None
```

## services/auth_service.py（🟡，业务编排）

```python
def register(db: Session, *, phone: str, password: str, tenant_name: str) -> RegisterResult
    # 校验强度 → 查重 → 单事务建 user+tenant+admin member → commit/rollback
    # raise: PhoneAlreadyExists / WeakPassword / InvalidPhone

def login(db: Session, *, phone: str, password: str) -> LoginResult
    # 查锁定(Redis) → 校验密码 → 失败 INCR/锁定 → 成功清零 + 取 admin member 角色 + 签 JWT
    # raise: AccountLocked(remaining_sec) / InvalidCredentials

def logout(*, jti: str, exp: int) -> None
    # blocklist_token(jti, ttl=exp-now)
```

契约要点：service 接收 `db: Session`（由 API 层 `Depends(get_db)` 注入），自身不 import FastAPI 对象（原则 I：service 不碰 Request/Response）。

## middlewares/auth_middleware.py（🔴）

```python
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(request, call_next):
        # 公开路径直接放行
        # 取 Bearer → decode_access_token → None 则 401
        # is_blocklisted(jti) 则 401
        # request.state.user_id/role/jti = claims
```

## middlewares/tenant_middleware.py（🔴）

```python
class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(request, call_next):
        # 若已鉴权：request.state.tenant_id = claims 里的 tenant_id
        # 绝不读 body/query 的 tenant_id
```

## api/deps.py（🔴）

```python
def get_current_user(request: Request, db=Depends(get_db)) -> User
    # 从 request.state.user_id 加载 User；不存在→401

def require_role(*roles: str) -> Callable    # 依赖工厂
    # 校验 request.state.role in roles；否则 403

def require_tenant() -> Callable             # 依赖工厂
    # 加载 tenant by request.state.tenant_id；closed→403；返回 Tenant
```

## 中间件注册顺序（main.py）

FastAPI `add_middleware` **后注册先执行**。逻辑入站顺序需 access_log→auth→tenant→rate_limit，故注册顺序（代码书写顺序）为**逆序**：

```python
app.add_middleware(RateLimitMiddleware)   # 最后执行
app.add_middleware(TenantMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(AccessLogMiddleware)   # 最先执行
```

本 feature 只需挂 Auth + Tenant（rate_limit/access_log 属其他日程，可留 TODO）。
