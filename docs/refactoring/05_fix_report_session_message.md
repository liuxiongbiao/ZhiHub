# 05 修复报告：`POST /api/v1/session/message` 返回 500

> 日期：2026-11-13
> 授权：项目负责人已授权对该接口进行一次最小范围修复
> 分支：`fix/session-message-route-7c6496b`（从 `main` @ `7c6496b` 切出，**未推送、未合并**）

---

## 1. 问题提交分析：`7c6496b`

| 项 | 内容 |
|---|---|
| 提交 | `7c6496b` refactor: 拆开超长流程并收拢重复文案，保持对外行为不变（2026-11-04，已在 `origin/main`） |
| 父提交 | `d8f86ec` test: 账单契约按当前 UTC 月读取用量 |
| 规模 | 63 个文件，+4656 / −3563 |
| 与本问题相关的改动 | `backend/app/api/v1/session.py`：把 `send_message` 内的限流检查和异常映射分别抽成 `_reject_busy_user`、`_message_http_error` 两个辅助函数 |

### 1.1 直接原因

新的辅助函数被插在了路由装饰器和 `send_message` **之间**：

```python
# ─── POST /session/message ───
@router.post("/message", response_model=SendMessageResponse)   # 装饰器
def _reject_busy_user(request: Request) -> None:                # ← 被装饰的变成了它
    ...

def _message_http_error(exc, session_id) -> HTTPException:
    ...

def send_message(body: SendMessageRequest, request, db=...):   # ← 失去装饰器，成为无人调用的普通函数
    ...
```

Python 装饰器只作用于紧随其后的函数，因此 FastAPI 把 `_reject_busy_user` 注册成了 `POST /api/v1/session/message` 的处理函数。

### 1.2 为什么智能体流程没有执行

| 环节 | 父提交（`send_message`） | `7c6496b`（`_reject_busy_user`） |
|---|---|---|
| 请求体解析 | 解析 `SendMessageRequest`，不合法返回 422 | **不解析**请求体（函数没有 body 参数） |
| 会话加载 | 按 `(session_id, tenant_id)` 查询，不存在返回 404 | 不执行 |
| 人工接管检查 | `ensure_agent_execution_allowed`，人工接管中返回 409 | 不执行 |
| 用户限流 | 超限返回 429 | **执行**（这是该函数仅有的逻辑） |
| 智能体调用 | `agent_service.handle_message(...)` | **不执行** |
| 响应 | `SendMessageResponse` | 返回 `None` → 不符合 `response_model` → `ResponseValidationError` → **500** |

所以：未触发限流的请求一律 500；触发限流的请求仍返回 429。这也是限流相关测试（`test_message_endpoint_429_not_persisted`、`integration/test_session_lifecycle_e2e.py:154-163`）照常通过、问题没有被它们发现的原因。

### 1.3 影响范围核查

**路由表对比**：在临时 worktree 中分别加载父提交和 `7c6496b` 的应用并导出全部路由（递归展开子路由），两边都是 110 个路由，**唯一的差异**就是这一条：

```
POST | /api/v1/session/message | app.api.v1.session | send_message        (d8f86ec)
POST | /api/v1/session/message | app.api.v1.session | _reject_busy_user   (7c6496b)
```

`7c6496b` 中也没有其他以下划线开头的函数被注册为路由。其他 API 的路由绑定不受影响。

**OpenAPI 对比**：只有该接口变化：请求体 `SendMessageRequest` 和 422 响应从文档中消失，`operationId` 由 `send_message_api_v1_session_message_post` 变为 `_reject_busy_user_api_v1_session_message_post`，`summary` 由 `Send Message` 变为 ` Reject Busy User`。依据 OpenAPI 生成客户端的外部调用方会受影响。

**调用方**：

| 调用方 | 位置 | 影响 |
|---|---|---|
| 管理后台聊天窗口（降级路径） | `frontend/src/components/chat/ChatWindow.vue:208-214`（经 `frontend/src/api/session.ts:33-39` 的 `sendMessage`，别名 `sendMessageApi`） | 默认走流式接口不受影响；流式返回 404/405/501 或发生网络类错误时降级到本接口，用户看到“发送失败” |
| 端到端验收脚本 | `backend/scripts/verify_e2e.py:102` | 失败 |
| 真实 MySQL 冒烟脚本 | `backend/scripts/real_mysql_smoke.py:291` | 失败 |
| 前端 E2E | `frontend/tests/e2e/e2e-fresh.cjs:72-75` | 失败 |
| 外部 API 调用方 | — | 失败 |
| 流式接口 `/session/message/stream`、Widget 接口 `/widget/{id}/message(/stream)` | `session.py`、`widget.py` | 不受影响（各自独立的处理函数，路由表对比无变化） |

**同一提交中的其他改动**：该提交还重写了 `agent_service.handle_message` 等大量代码。修复前完整测试中只有这 2 个用例失败，Widget 非流式接口也调用 `handle_message`，相关用例通过；本次新增的端到端回归测试也走通了重写后的 `handle_message`。其余改动不在本次修复范围内，未做逐行审查。

---

## 2. 修复方案

| 项 | 内容 |
|---|---|
| 修改文件 | `backend/app/api/v1/session.py`（**仅移动一行装饰器**） |
| 新增文件 | `backend/tests/contracts/test_session_message_route.py`（回归测试） |
| 未修改 | 其他业务模块、数据库结构、账单、支付、租户权限、密钥、认证规则；辅助函数 `_reject_busy_user`、`_message_http_error` 保持原样 |

```diff
 # ─── POST /session/message ───
-@router.post("/message", response_model=SendMessageResponse)
 def _reject_busy_user(request: Request) -> None:
 ...
+@router.post("/message", response_model=SendMessageResponse)
 def send_message(
     body: SendMessageRequest,
```

**行为等价性**：修复后 `send_message` 的执行顺序（加载会话 → 人工接管检查 → 限流 → `handle_message`）与异常映射（`SessionNotFound`→404、`SessionHumanControlled`→409、`TenantInactive`→403、其他→500 且不泄露异常原文）与父提交逐项一致。

**可能的副作用**：接口恢复调用智能体后，会重新产生 LLM 调用、消息落库和会话计费标记，这是恢复原有行为，属于预期。`_reject_busy_user` 不再作为路由暴露。

---

## 3. 回归测试

`backend/tests/contracts/test_session_message_route.py`，5 个用例：

| 用例 | 验证内容 |
|---|---|
| `test_route_is_bound_to_send_message` | 路由处理函数是 `send_message` |
| `test_openapi_keeps_send_message_request_body` | OpenAPI 请求体仍为 `SendMessageRequest`，且保留 422 响应 |
| `test_missing_content_is_rejected_by_request_validation` | 缺少 `content` 返回 422（请求体校验恢复） |
| `test_unknown_session_returns_404` | 不存在的会话返回 404（会话加载恢复） |
| `test_message_runs_agent_graph_and_persists_reply` | **完整智能体调用链**：只给 LangGraph 图执行和 Mongo trace 写入打桩，其余走真实代码。断言：返回 200 与正确的 `SendMessageResponse`（内容、引用来源、元数据）；图恰好被调用一次，收到正确的 `session_id`、`tenant_id` 和用户消息；数据库中按顺序落库 user 与 assistant 两条消息，返回的 `message_id` 与 assistant 消息一致；会话状态变为 `agent_handling`，`effective_reply=True` |

**证明测试有效**：把该测试文件放到未修复的 `7c6496b` 临时 worktree 中运行，**5 个全部失败**；在修复分支上 **5 个全部通过**。

---

## 4. 验证结果

| 验证项 | 结果 |
|---|---|
| 新增回归测试 + 原失败的 2 个契约测试 | 7 passed |
| 修复分支路由表 vs 父提交 `d8f86ec` | **完全一致**（110 个路由） |
| 修复分支完整 OpenAPI 文档 vs 父提交 `d8f86ec` | **完全一致** |
| 完整后端测试套件（`pytest tests`） | **863 passed, 2 skipped, 0 failed**，24 分 14 秒 |
| 覆盖率 | 78.9%（修复前 78.7%） |

完整套件对比：

| | 修复前（`7c6496b`） | 修复后 |
|---|---|---|
| 用例总数 | 860 | 865（+5 回归测试） |
| 通过 | 856 | 863 |
| 失败 | 2 | 0 |
| 跳过 | 2 | 2（`test_alembic_from_scratch`，需 `MYSQL_ROOT_PASSWORD`） |

运行环境：本机 `backend/.venv`（Python 3.11.9，镜像与 CI 为 3.12），环境变量与 CI 一致（`APP_ENV=test`、`APP_SECRET_KEY=ci-only-secret-key`），本机未运行 MySQL、Redis、MongoDB 等服务。**未运行**：前端测试、Playwright E2E、`scripts/verify_e2e.py` 与 `scripts/real_mysql_smoke.py`（需要真实运行环境），建议在预发环境合并前执行。

---

## 5. 分支与提交

- 分支 `fix/session-message-route-7c6496b` 基于 `main` @ `7c6496b`，保留全部原有提交历史。
- 提交 1：修复 + 回归测试（可单独 cherry-pick）。
- 提交 2：第一阶段审计文档及本修复报告。
- **未推送到任何远端，未合并到 `main`。** 合并方式（PR / 直接合并 / cherry-pick）等待您的指示。
