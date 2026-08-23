# 智枢 ZhiHub V1.4 —— 后端项目架构与目录结构规约

**文档类型**：架构设计文档  
**对应产品需求**：《智枢 ZhiHub V1.4 PRD》  
**范围**：仅后端（`backend/`），前端不在本文档范围内  

---

## 1. 架构分层模型

```
┌─────────────────────────────────────────────────────────────────┐
│                     API 路由层 (Presentation)                     │
│  职责：接收 HTTP 请求 → 参数校验 → 调用 Service → 返回响应         │
│  禁止：直接操作数据库、包含业务逻辑、调用外部基础设施                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                    中间件层 (Middleware)                          │
│  职责：请求预处理（鉴权、租户上下文注入、限流、日志）                  │
│  禁止：包含业务逻辑、直接操作数据库                                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                   业务逻辑服务层 (Service)                         │
│  职责：编排业务流程、调用 CRUD + Agent + MCP + 外部基础设施           │
│  禁止：直接处理 HTTP 请求/响应对象、直接写 SQL                       │
└──────┬──────────────┬──────────────┬──────────────┬─────────────┘
       │              │              │              │
┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼──────────────┐
│  CRUD 层    │ │ Agent 层  │ │ MCP 层    │ │ 异步任务层 (Tasks)  │
│ 数据持久化   │ │ 智能体编排 │ │ 外部集成   │ │ Celery + RabbitMQ  │
│ 禁止：      │ │ 禁止：     │ │ 禁止：     │ │ 禁止：             │
│ 业务逻辑    │ │ 直接操作DB │ │ 直接操作DB │ │ 处理 HTTP 请求     │
└──────┬──────┘ └─────┬─────┘ └─────┬─────┘ └─────┬──────────────┘
       │              │              │              │
┌──────▼──────────────▼──────────────▼──────────────▼──────────────┐
│                     核心基础设施层 (Core)                          │
│  安全 / 缓存 / 消息队列 / 限流 / 幂等 / 异常 / 分页                  │
└──────────────────────────────────────────────────────────────────┘
```

### 1.1 各层职责边界

| 层级 | 一句话职责 | 可以做的事 | 绝对不能做的事 |
|---|---|---|---|
| **API 路由层** | 接收请求，返回响应 | 参数校验、路由分发、调用 Service、组装 HTTP 响应 | 写业务逻辑、直接操作数据库、调用外部 API |
| **中间件层** | 请求预处理 | JWT 校验、租户上下文注入、限流、trace_id 生成、访问日志 | 包含任何业务判断逻辑 |
| **Service 层** | 业务编排中枢 | 调用 CRUD/Agent/MCP、事务编排、状态机流转、配额管理 | 直接操作 `Request`/`Response` 对象、写裸 SQL |
| **CRUD 层** | 数据持久化 | 封装 SQLAlchemy 查询、分页、基础增删改查 | 包含业务判断逻辑（那是 Service 层的事） |
| **Agent 层** | AI 智能体编排 | LangGraph 状态图、Prompt 模板、LLM 调用、工具调用决策 | 直接操作数据库（应通过 CRUD 或 MCP）、处理 HTTP |
| **MCP 层** | 外部系统集成 | 标准 MCP 协议实现、工具定义、参数校验、幂等处理 | 包含 Agent 编排逻辑、直接操作其他服务的数据库 |
| **异步任务层** | 耗时/定时任务 | 文档解析入库、账单生成、通知发送、数据清理 | 处理 HTTP 请求（那是 API 层的事） |
| **Core 层** | 通用基础设施 | 安全工具、Redis 操作、RabbitMQ 工具、异常定义 | 包含任何业务特定逻辑 |

### 1.2 数据流向

**请求向（读/写）**：
```
HTTP Request
  → Middlewares（鉴权 → 租户注入 → 限流 → 日志）
    → API v1 Router（参数校验）
      → Service（业务编排）
        ├─→ CRUD → MySQL
        ├─→ Agent → LangGraph → LLM / MCP Server
        ├─→ MCP → Mock 业务系统
        ├─→ Core（Redis 配额/缓存、RabbitMQ 发布）
        └─→ MongoDB（Trace 写入）
      ← 返回业务结果
    ← 组装 HTTP Response
  ← 响应用户
```

**异步向（后台任务）**：
```
RabbitMQ Message
  → Celery Worker
    → Task 函数
      ├─→ RagFlow（文档解析）
      ├─→ Milvus / ES（索引写入）
      ├─→ MySQL（状态更新）
      ├─→ Redis（计数器落库）
      └─→ 邮件/短信通知
```

---

## 2. 全景目录结构树

```
backend/                                     # 后端项目根目录
│   # 职责：整个后端应用的物理根，所有 Python 源码、配置、迁移脚本、
│   #       测试、部署文件均在此目录下
│   # 禁止：放置前端资源、与 Python 运行时无关的非代码文件
│
├── .env.example                             # 环境变量模板
│   # 职责：列出所有必需的环境变量及其示例值，供开发者复制为 .env 使用
│   # 可放置：数据库连接串、API Key、JWT 密钥等非默认配置项的名称占位
│   # 禁止：写入真实密钥/密码（应通过 .env 注入，.env 不入库）
│
├── requirements.txt                         # Python 依赖清单
│   # 职责：声明项目运行所需的所有 PyPI 包及其版本范围
│   # 可放置：fastapi、sqlalchemy、langgraph、litellm、celery 等
│   # 禁止：将开发-only 工具（如 pytest、ruff）与生产依赖混在同一文件
│   #        ——开发依赖应放入 pyproject.toml 的 [dev] 组
│
├── pyproject.toml                           # 项目元数据与工具配置
│   # 职责：项目名称、版本、Python 版本约束，以及 ruff/black/mypy
│   #       等开发工具的配置集中入口
│   # 禁止：放运行时依赖版本（运行时依赖在 requirements.txt）
│
├── alembic.ini                              # Alembic 数据库迁移配置
│   # 职责：指定 alembic 脚本位置、数据库连接方式（引用环境变量）
│   # 禁止：硬编码数据库连接字符串
│
├── Dockerfile                               # 后端服务容器镜像构建文件
│   # 职责：定义后端应用的容器化构建步骤
│   # 禁止：包含 docker-compose 的服务编排逻辑（那属于 deploy/）
│
├── celery_worker.py                         # Celery Worker 启动入口
│   # 职责：创建 Celery 应用实例，注册所有异步任务模块，供 Worker 进程启动
│   # 可放置：Celery app 配置、任务模块自动发现
│   # 禁止：定义具体的异步任务逻辑（任务实现在 app/tasks/ 下）
│
│
├── app/                                     # 应用主包
│   # 职责：后端所有业务代码的唯一容器，包含路由、服务、模型、Agent 等全部子模块
│   # 禁止：在此目录下放置非 Python 文件（除 Agent Prompt 模板外）
│   │
│   ├── __init__.py
│   │
│   ├── main.py                              # FastAPI 应用入口
│   │   # 职责：创建 FastAPI 实例，注册所有中间件、路由、异常处理器、
│   │   #       CORS 配置、生命周期事件（启动/关闭时的资源初始化与释放）
│   │   # 可放置：app = FastAPI()、app.include_router()、app.add_middleware()
│   │   # 禁止：包含任何业务逻辑、直接导入 Service/CRUD（应通过路由注册间接引用）
│   │
│   ├── config.py                            # 集中配置类
│   │   # 职责：基于 pydantic-settings，从环境变量读取并校验全部配置项，
│   │   #       包括数据库连接、Redis、RabbitMQ、JWT、模型 API Key、
│   │   #       各服务地址、套餐配额默认值等。所有其他模块统一从此处获取配置
│   │   # 可放置：Settings 类、各子配置分组类
│   │   # 禁止：硬编码任何密钥/地址、包含业务逻辑
│   │
│   │
│   ├── api/                                 # API 路由层（表示层）
│   │   # 职责：定义所有对外暴露的 REST 接口，是系统的"协议边界"
│   │   # 可放置：FastAPI Router、路径装饰器、参数声明（Query/Body/Path）、
│   │   #         状态码、响应模型声明
│   │   # 禁止：任何数据库查询、业务逻辑（调用 Service 即可）、
│   │   #       直接 import Model（应通过 Schema 中转）
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── deps.py                          # 共享依赖注入
│   │   │   # 职责：定义 FastAPI Depends() 可复用的依赖项——
│   │   │   #       数据库 session 获取、当前登录用户解析、
│   │   │   #       租户身份校验、角色权限校验、分页参数解析
│   │   │   # 可放置：get_db()、get_current_user()、require_role()、
│   │   │   #       require_tenant()、PaginationParams
│   │   │   # 禁止：包含业务逻辑（只做身份与权限的提取与校验）
│   │   │
│   │   └── v1/                              # API v1 版本路由组
│   │       # 职责：v1 版本的全部路由定义，按业务模块拆分为独立文件
│   │       # 可放置：APIRouter 实例、各 HTTP method 端点函数
│   │       # 禁止：跨模块直接调用（如 auth.py 不能 import order.py 的路由）
│   │       │
│   │       ├── __init__.py                  # 汇总所有子路由并挂载到统一前缀
│   │       │
│   │       ├── auth.py                      # 认证相关接口
│   │       │   # 职责：注册（手机号/邮箱）、登录（密码/验证码/微信扫码）、
│   │       │   #       登出（token 黑名单）、验证码发送、密码找回
│   │       │   # 禁止：直接操作 User 表（通过 auth_service）
│   │       │
│   │       ├── tenant.py                    # 租户与成员管理接口
│   │       │   # 职责：租户信息查询、配置向导状态、套餐变更、成员邀请与列表
│   │       │   # 禁止：跨模块业务（邀请坐席时不应在此处发送通知——
│   │       │   #       那是 tenant_service 调用 notification_tasks 的事）
│   │       │
│   │       ├── knowledge.py                 # 知识库管理接口
│   │       │   # 职责：文档上传、列表查询、删除、重建索引、检索测试台
│   │       │   # 禁止：直接调用 Milvus/ES（通过 knowledge_service → retrieval_service）
│   │       │
│   │       ├── session.py                   # 会话与对话接口
│   │       │   # 职责：会话列表、会话详情、用户发送消息、转人工触发、
│   │       │   #       坐席认领（分布式锁）、坐席退回、留言建单、👍/👎反馈
│   │       │   # 禁止：直接调用 LLM 或 Agent（通过 agent_service + session_service）
│   │       │
│   │       ├── ticket.py                    # 工单管理接口
│   │       │   # 职责：工单列表、创建工单（幂等）、状态更新
│   │       │   # 禁止：直接操作 MCP 协议（通过 ticket_service → mcp_service）
│   │       │
│   │       ├── order.py                     # 订单与售后管理接口
│   │       │   # 职责：订单列表与详情查询、发货操作（乐观锁）、
│   │       │   #       退款申请（幂等）、退款审批（追加记录不可覆写）
│   │       │   # 禁止：直接操作支付宝沙箱（通过 order_service）
│   │       │
│   │       ├── billing.py                   # 计量计费接口
│   │       │   # 职责：当月用量查询、账单列表、支付发起、支付回调（幂等去重）
│   │       │   # 禁止：直接操作 Redis 计数器（通过 billing_service）
│   │       │
│   │       ├── agent.py                     # Agent 可观测性与管理接口
│   │       │   # 职责：会话 Agent trace 查询、Bad Case 列表、
│   │       │   #       DeepAgent 任务发起与状态查询、系统降级状态查询
│   │       │   # 禁止：直接查询 MongoDB trace（通过 agent_service）
│   │       │
│   │       ├── widget.py                    # Widget 配置接口
│   │       │   # 职责：Widget 品牌配置（主题色/Logo/欢迎语）读写、
│   │       │   #       嵌入脚本生成
│   │       │   # 禁止：包含前端渲染逻辑（那属于前端 widget 项目）
│   │       │
│   │       └── mcp/                         # MCP Server 端点的 HTTP 暴露
│   │           # 职责：以 FastAPI 子应用形式暴露 MCP Server 的 HTTP 传输层，
│   │           #       供外部 Agent 通过标准 MCP 协议调用
│   │           # 可放置：MCP Server 初始化、工具注册
│   │           # 禁止：在此处实现工具的业务逻辑（业务逻辑在 app/mcp_servers/）
│   │           │
│   │           ├── __init__.py
│   │           ├── server.py                # MCP Server 实例创建与挂载
│   │           └── tools.py                 # MCP 工具注册清单（声明，不实现）
│   │
│   │
│   ├── middlewares/                         # 中间件层
│   │   # 职责：请求处理管道的横切关注点，对路由层完全透明
│   │   # 可放置：ASGI 中间件 / FastAPI 中间件
│   │   # 禁止：在此处修改请求体（只读或仅注入上下文属性）
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── access_log_middleware.py         # 访问日志中间件
│   │   │   # 职责：为每个请求生成 trace_id，记录请求方法、路径、耗时、
│   │   │   #       响应状态码，注入到日志上下文
│   │   │   # 禁止：记录请求体中的 PII 字段明文
│   │   │
│   │   ├── auth_middleware.py               # 认证中间件
│   │   │   # 职责：解析 Authorization 头的 JWT → 校验签名与过期时间 →
│   │   │   #       查 Redis 黑名单（登出失效）→ 注入 current_user 到 request.state
│   │   │   # 禁止：做角色权限判断（那是 deps.py 的职责，中间件只验证"是谁"）
│   │   │
│   │   ├── tenant_middleware.py             # 租户上下文中间件
│   │   │   # 职责：从请求头 / API Key / JWT claims 中提取 tenant_id，
│   │   │   #       注入 request.state，确保下游所有查询都携带租户过滤
│   │   │   # 禁止：校验租户状态（那是 deps.py 的职责）
│   │   │
│   │   └── rate_limit_middleware.py         # 限流中间件
│   │       # 职责：基于 request.state 的 tenant_id 和 user_id，
│   │       #       调用 rate_limit_service 执行租户级 + 终端用户级双重令牌桶校验
│   │       # 禁止：包含硬编码的限流阈值（应从 config.py 读取）
│   │
│   │
│   ├── models/                              # 物理数据模型层（SQLAlchemy ORM）
│   │   # 职责：定义 MySQL 表结构与 Python 对象之间的映射关系。
│   │   #       **这是唯一允许定义数据库表结构的地方**
│   │   # 可放置：SQLAlchemy declarative_base 子类、Column 定义、
│   │   #         relationship、表级约束、混合属性（hybrid_property）
│   │   # 禁止：包含业务逻辑、API 序列化逻辑（那是 Schema 层的事）、
│   │   #       MongoDB/Milvus/ES 的数据结构（各自独立管理）
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── base.py                          # ORM 基类
│   │   │   # 职责：定义所有模型的公共字段（id、created_at、updated_at）
│   │   │   #       以及 __tablename__ 自动生成规则、通用方法（如 to_dict）
│   │   │   # 禁止：包含业务模型特有的字段或方法
│   │   │
│   │   ├── user.py                          # User 表
│   │   │   # 职责：平台账号（管理员/坐席），含密码哈希、手机号/邮箱（AES 加密存储）、
│   │   │   #       微信 openid（加密）、登录失败计数、账号状态
│   │   │   # 禁止：存储明文密码、明文手机号/邮箱
│   │   │
│   │   ├── tenant.py                        # Tenant 表
│   │   │   # 职责：租户（企业客户）的套餐、配额、存储上限、退款审批阈值、
│   │   │   #       Widget 品牌配置、状态（正常/只读/已注销）
│   │   │   # 禁止：存储租户成员信息（在 tenant_member.py 的关联表中）
│   │   │
│   │   ├── tenant_member.py                 # TenantMember 关联表
│   │   │   # 职责：租户与用户的多对多关联，携带角色枚举（admin / agent）
│   │   │   # 禁止：在此表中冗余存储用户的手机号/密码等（通过 relationship 关联 User）
│   │   │
│   │   ├── verification_code.py             # VerificationCode 表
│   │   │   # 职责：短信/邮箱验证码的发送记录，含目标、用途、过期时间、
│   │   │   #       验证失败次数（连续 3 次错误即失效）
│   │   │   # 禁止：用于存储其他类型的临时 token（如密码重置 token——
│   │   │   #       应单独建表或放 Redis）
│   │   │
│   │   ├── knowledge_document.py            # KnowledgeDocument 表
│   │   │   # 职责：知识库文档元数据——文件名、大小、状态（待处理/处理中/
│   │   │   #       已就绪/解析失败/待人工复核/索引异常）、版本号、
│   │   │   #       是否为最新版本、过期时间、质量状态
│   │   │   # 禁止：存储文档的切片内容（在 Milvus/ES 中）或向量（在 Milvus 中）
│   │   │
│   │   ├── knowledge_chunk.py               # KnowledgeChunk 表
│   │   │   # 职责：切片元数据索引——记录每个切片对应的 doc_id、
│   │   │   #       在 Milvus/ES 中的唯一 ID、切片序号、字符数
│   │   │   # 禁止：存储切片全文内容或向量（仅存元数据，内容在 Milvus/ES）
│   │   │
│   │   ├── session.py                       # Session 表
│   │   │   # 职责：会话状态机——状态（CREATED/ROUTING/AGENT_HANDLING/
│   │   │   #       WAITING_HUMAN/HUMAN_HANDLING/RESOLVED/CLOSED）、
│   │   │   #       情绪标记、意图类型、分配坐席、version（乐观锁字段）
│   │   │   # 禁止：存储完整对话内容（在 MongoDB session_traces 中）
│   │   │
│   │   ├── message.py                       # Message 表
│   │   │   # 职责：单条聊天消息——发送者类型（user/agent/human/system）、
│   │   │   #       内容、时间戳、所属 session
│   │   │   # 禁止：存储 Agent 内部思考链（那属于 MongoDB trace）
│   │   │
│   │   ├── ticket.py                        # Ticket 表（Mock 工单系统）
│   │   │   # 职责：工单状态机——状态（OPEN→ASSIGNED→PROCESSING→
│   │   │   #       RESOLVED→CLOSED，可 REOPENED）、描述、优先级、
│   │   │   #       处理人、client_request_id（幂等键，唯一索引）
│   │   │   # 禁止：存储工单处理过程中的 Agent 调用日志
│   │   │
│   │   ├── order.py                         # Order 表（Mock 订单系统）
│   │   │   # 职责：完整订单履约状态机——PENDING_PAYMENT / PAID /
│   │   │   #       PROCESSING / SHIPPED / COMPLETED / CANCELLED /
│   │   │   #       REFUND_REQUESTED / REFUNDING / REFUNDED，
│   │   │   #       金额、加密手机号、物流信息、取消原因、
│   │   │   #       version（乐观锁字段，每次状态变更自增）
│   │   │   # 禁止：在退款场景下直接修改金额字段（退款走 refund_approvals 表）
│   │   │
│   │   ├── refund_approval.py               # RefundApproval 表
│   │   │   # 职责：退款审批记录——所有记录只追加（INSERT only）、
│   │   │   #       不可 UPDATE 已有审批结论、不可 DELETE。
│   │   │   #       字段：申请金额、审批人、审批意见、审批决定、时间戳
│   │   │   # 禁止：对此表执行 UPDATE 或 DELETE 操作
│   │   │
│   │   ├── usage_record.py                  # UsageRecord 表
│   │   │   # 职责：按天聚合的用量明细——每日对话数、Token 消耗量、
│   │   │   #       使用的模型、是否超额标记
│   │   │   # 禁止：存储实时计数值（实时计数在 Redis quota:* key 中）
│   │   │
│   │   ├── billing_invoice.py               # BillingInvoice 表
│   │   │   # 职责：月度账单——基础套餐费、超额费、支付状态、
│   │   │   #       payment_trade_no（支付宝交易号，唯一索引用于幂等去重）、支付时间
│   │   │   # 禁止：手动修改已支付账单的金额字段
│   │   │
│   │   └── csat.py                          # CsatEvaluation 表
│   │       # 职责：满意度评价与回答反馈——CSAT 评分、👍/👎、Bad Case 标记
│   │       # 禁止：存储 Agent trace 详情（trace 在 MongoDB）
│   │
│   │
│   ├── schemas/                             # 传输模型层（Pydantic DTO）
│   │   # 职责：定义 API 请求与响应的数据结构，是"外部契约"。
│   │   #       每一个 API 端点入参/出参必须在此处有明确的 Schema 定义。
│   │   # 可放置：Pydantic BaseModel 子类、Field 校验、自定义 validator、
│   │   #         响应泛型包装类（ApiResponse[T]、PageResult[T]）
│   │   # 禁止：直接继承 SQLAlchemy Model（应手动映射字段）、
│   │   #       包含数据库查询逻辑、包含业务逻辑
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── common.py                        # 通用响应结构
│   │   │   # 职责：统一 API 响应格式——ApiResponse[T]（code/message/data）、
│   │   │   #       PageResult[T]（items/total/page/page_size）、
│   │   │   #       错误响应模型
│   │   │   # 禁止：在通用结构中加入业务字段
│   │   │
│   │   ├── auth.py                          # 认证相关 DTO
│   │   │   # 职责：注册/登录请求体、Token 响应、用户信息响应、
│   │   │   #       验证码发送请求、密码重置请求
│   │   │   # 禁止：在响应中返回密码哈希或加密前的手机号
│   │   │
│   │   ├── tenant.py                        # 租户相关 DTO
│   │   │   # 职责：租户信息、套餐信息、配置向导状态、成员列表项
│   │   │   # 禁止：泄露其他租户的数据
│   │   │
│   │   ├── knowledge.py                     # 知识库相关 DTO
│   │   │   # 职责：文档上传响应、文档列表项、文档状态、检索测试请求/响应
│   │   │   # 禁止：在响应中返回切片全文（仅返回元数据）
│   │   │
│   │   ├── session.py                       # 会话相关 DTO
│   │   │   # 职责：会话列表项、消息请求/响应、转人工请求、会话状态枚举
│   │   │   # 禁止：暴露内部 trace_id（那是 agent.py Schema 的事）
│   │   │
│   │   ├── ticket.py                        # 工单相关 DTO
│   │   │   # 职责：工单创建/更新请求、工单列表项、状态枚举
│   │   │   # 禁止：在请求体中允许绕过状态机直接跳转非法状态
│   │   │
│   │   ├── order.py                         # 订单相关 DTO
│   │   │   # 职责：订单查询响应、发货请求、退款申请请求、
│   │   │   #       退款审批请求、订单状态枚举
│   │   │   # 禁止：暴露加密存储的手机号明文（应在 Service 层脱敏后返回）
│   │   │
│   │   └── billing.py                       # 计费相关 DTO
│   │       # 职责：用量概览响应、账单列表项、支付回调请求体
│   │       # 禁止：暴露其他租户的用量或账单数据
│   │
│   │
│   ├── crud/                                # 数据访问层（Data Access）
│   │   # 职责：封装对 MySQL 的纯数据库操作，是 Service 层与 ORM 之间
│   │   #       的唯一通道。每个文件对应一个核心业务实体
│   │   # 可放置：SQLAlchemy 查询构建、过滤条件、排序、分页、
│   │   #       基础增删改查、批量操作
│   │   # 禁止：包含业务判断逻辑（如"是否可以创建工单"——
│   │   #       那是 Service 层根据套餐/角色判断的）、
│   │   #       调用外部服务、操作 Redis/MongoDB/Milvus/ES
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── base.py                          # 通用 CRUD 基类
│   │   │   # 职责：提供 get / get_multi / create / update / delete /
│   │   │   #       get_or_create 等通用方法，所有具体 CRUD 类继承自此
│   │   │   # 禁止：包含任何业务实体特定的查询条件
│   │   │
│   │   ├── user.py                          # 用户数据访问
│   │   │   # 职责：按手机号/邮箱/openid 查询用户、创建用户、
│   │   │   #       更新登录失败计数、更新密码哈希
│   │   │   # 禁止：在此处做密码强度校验（那是 auth_service 的职责）
│   │   │
│   │   ├── tenant.py                        # 租户数据访问
│   │   │   # 职责：按 tenant_id 查询租户信息、更新套餐/状态/配置
│   │   │   # 禁止：在此处做套餐升级的业务校验（那是 tenant_service 的职责）
│   │   │
│   │   ├── knowledge.py                     # 知识库数据访问
│   │   │   # 职责：文档元数据 CRUD、按状态筛选、版本管理查询、
│   │   │   #       到期文档查询、存储用量统计
│   │   │   # 禁止：触发文档解析或索引操作（那是 knowledge_tasks 的事）
│   │   │
│   │   ├── session.py                       # 会话数据访问
│   │   │   # 职责：会话状态查询与更新（含乐观锁 version 校验）、
│   │   │   #       消息持久化、按状态/坐席筛选
│   │   │   # 禁止：在此处实现认领锁逻辑（那是 human_service 的职责，
│   │   │   #       需要 Redis 分布式锁，不仅是 DB 操作）
│   │   │
│   │   ├── ticket.py                        # 工单数据访问
│   │   │   # 职责：工单 CRUD、按 client_request_id 幂等去重查询、
│   │   │   #       按状态筛选
│   │   │   # 禁止：校验工单状态跃迁合法性（那是 ticket_service 的职责）
│   │   │
│   │   ├── order.py                         # 订单数据访问
│   │   │   # 职责：订单查询、状态更新（带 version 乐观锁）、
│   │   │   #       按状态/手机号筛选
│   │   │   # 禁止：在此处实现状态机跃迁规则校验（那是 order_service 的职责）
│   │   │
│   │   └── billing.py                       # 计费数据访问
│   │       # 职责：用量记录聚合查询、账单 CRUD、按支付状态筛选
│   │       # 禁止：在此处计算超额费用（那是 billing_service 的职责）
│   │
│   │
│   ├── services/                            # 业务逻辑服务层
│   │   # 职责：**系统所有业务逻辑的唯一实现位置**。
│   │   #       编排 CRUD、Agent、MCP、外部基础设施的调用，实现完整的
│   │   #       业务用例。每个文件对应一个业务领域
│   │   # 可放置：业务流程编排、事务管理、状态机校验、配额判断、
│   │   #       多步操作的原子性协调
│   │   # 禁止：直接操作 HTTP 请求/响应对象、直接写 SQL、
│   │   #       在 Service 之间形成循环依赖
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── auth_service.py                  # 认证业务逻辑
│   │   │   # 职责：注册流程（创建用户+租户+管理员关联的事务编排）、
│   │   │   #       登录校验（密码/验证码/微信 OAuth）、密码强度策略检查、
│   │   │   #       验证码发送限频逻辑、登录失败锁定判断、登出 token 黑名单写入
│   │   │   # 禁止：直接生成 JWT（调用 core/security.py）、直接发短信（调用 tasks）
│   │   │
│   │   ├── tenant_service.py                # 租户业务逻辑
│   │   │   # 职责：配置向导状态推进、套餐变更校验（含降级/升级规则）、
│   │   │   #       成员邀请链接生成与校验、租户注销数据处理
│   │   │   # 禁止：直接操作用户表（通过 user CRUD）
│   │   │
│   │   ├── knowledge_service.py             # 知识库业务逻辑
│   │   │   # 职责：文档上传前置校验（文件大小、格式、存储配额）、
│   │   │   #       文档状态流转管理（待处理→处理中→已就绪/解析失败/
│   │   │   #       待人工复核/索引异常）、重复上传的版本策略、
│   │   │   #       质量检测判定、到期文档自动下架触发、
│   │   │   #       重建索引任务发布
│   │   │   # 禁止：直接调用 RagFlow/Milvus/ES（通过 knowledge_tasks）
│   │   │
│   │   ├── retrieval_service.py             # 混合检索业务逻辑
│   │   │   # 职责：**混合检索的完整编排** ——查询改写（上下文补全指代）→
│   │   │   #       并行调用 Milvus 向量召回 + ES BM25 召回 →
│   │   │   #       RRF 融合排序 → bge-reranker 精排 →
│   │   │   #       置信度判断（低于阈值触发 HyDE 兜底检索）→
│   │   │   #       空知识库判断、降级模式标记
│   │   │   # 禁止：直接生成自然语言回答（那是 Agent 节点的职责）、
│   │   │   #       处理 HTTP 请求参数
│   │   │
│   │   ├── agent_service.py                 # Agent 编排中枢（主会话链路）
│   │   │   # 职责：接收用户消息，驱动 LangGraph 状态图执行——
│   │   │   #       调用 graph.py 的入口节点 → 等待状态机流转 →
│   │   │   #       收集 trace 信息 → 写入 MongoDB → 触发配额计数 →
│   │   │   #       处理 LLM 异常兜底 → 检测转人工条件 →
│   │   │   #       上下文 token 预算截断、单会话轮次上限检查
│   │   │   # 禁止：直接实现 Agent 节点的内部逻辑（节点实现在 agents/nodes/ 下）、
│   │   │   #       直接操作 Milvus/ES（通过 retrieval_service）
│   │   │
│   │   ├── session_service.py               # 会话生命周期管理
│   │   │   # 职责：会话创建、状态流转（CREATED→ROUTING→...→CLOSED）、
│   │   │   #       会话上下文 Redis 读写、空闲超时检测（30 分钟自动 RESOLVED）、
│   │   │   #       会话关闭后的 24 小时续聊规则判断
│   │   │   # 禁止：直接调用 LLM 或 Agent 节点
│   │   │
│   │   ├── human_service.py                 # 人工介入业务逻辑
│   │   │   # 职责：转人工条件汇总判断（置信度/情绪/敏感操作/连续未解决/
│   │   │   #       主动要求）、坐席认领 Redis 分布式锁互斥、
│   │   │   #       坐席退回给 Agent 的状态回退、无在线坐席时留言自动建单、
│   │   │   #       坐席快捷回复模板管理
│   │   │   # 禁止：直接操作会话消息表（通过 session CRUD）
│   │   │
│   │   ├── ticket_service.py                # 工单业务逻辑
│   │   │   # 职责：工单创建前的参数校验与幂等检查、状态机跃迁合法性校验、
│   │   │   #       工单与 MCP 调用的协调
│   │   │   # 禁止：直接实现 MCP 协议（通过 mcp_service）
│   │   │
│   │   ├── order_service.py                 # 订单履约业务逻辑
│   │   │   # 职责：**订单状态机的所有状态变迁规则** ——
│   │   │   #       可跃迁路径校验（拒绝非法跳转）、乐观锁 version 冲突处理、
│   │   │   #       退款金额上限校验（不超过订单实际支付金额）、
│   │   │   #       分级审批阈值判断、审批记录只追加不覆写、
│   │   │   #       撤销申请分支、超时自动确认收货/自动取消
│   │   │   # 禁止：直接调用支付宝沙箱接口（通过 billing_service 或独立 payment 模块）
│   │   │
│   │   ├── mcp_service.py                   # MCP 工具调用协调
│   │   │   # 职责：MCP 工具权限矩阵校验（套餐+角色→工具白名单）、
│   │   │   #       写操作幂等键（client_request_id）去重判断、
│   │   │   #       入参格式校验（金额正数、ID 格式、字符串长度）、
│   │   │   #       MCP Server 调用代理、熔断状态监控与降级切换、
│   │   │   #       终端用户双因子校验调度（手机号+订单号/工单号后4位 或 验证码）
│   │   │   # 禁止：实现 MCP Server 的内部业务逻辑（那在 app/mcp_servers/ 下）
│   │   │
│   │   ├── billing_service.py               # 计量计费业务逻辑
│   │   │   # 职责：配额计数器管理（Redis INCR + 按套餐阈值判断）、
│   │   │   #       超额标记（不拒绝服务）、超额费用累加、
│   │   │   #       80%/100% 配额预警触发、多模型折算系数计算、
│   │   │   #       账单生成（基础费+超额费）、支付回调幂等处理（按 trade_no 去重）、
│   │   │   #       逾期降级判断、用量导出
│   │   │   # 禁止：直接发起支付宝沙箱支付（那是独立支付模块的职责，
│   │   │   #       或在此处编排支付流程但不实现沙箱协议细节）
│   │   │
│   │   ├── rate_limit_service.py            # 限流服务
│   │   │   # 职责：租户级 + 终端用户级双维度令牌桶算法的实现，
│   │   │   #       从 Redis 读取/更新令牌，返回是否允许通过
│   │   │   # 禁止：在此处定义各套餐的限流阈值（阈值在 config.py 或 tenants 表）
│   │   │
│   │   ├── idempotency_service.py           # 幂等服务
│   │   │   # 职责：通用幂等键的 Redis 写入/查询/结果缓存，
│   │   │   #       供 MCP 写操作、支付回调、MQ 消费者复用
│   │   │   # 禁止：在此处定义哪些操作需要幂等（由各调用方自行判断）
│   │   │
│   │   └── export_service.py                # 数据导出服务
│   │       # 职责：按租户/月份生成用量与 Token 消耗明细 CSV、
│   │       #       租户注销前全量数据打包（JSON/CSV）
│   │       # 禁止：直接访问数据库（通过各 CRUD 模块获取数据）
│   │
│   │
│   ├── agents/                              # AI 智能体（Agent）模块
│   │   # 职责：多智能体系统的全部 AI 相关实现——
│   │   #       LangGraph 状态图定义、各 Agent 节点、Prompt 模板、
│   │   #       DeepAgent 自主规划实现。这是本项目的核心差异化模块
│   │   # 可放置：LangGraph StateGraph、节点函数、LLM 调用、工具绑定
│   │   # 禁止：直接操作数据库（通过 Service/CRUD 层）、
│   │   #       直接处理 HTTP 请求、直接在节点内实现 MCP 工具的业务逻辑
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── graph.py                         # LangGraph 状态图定义
│   │   │   # 职责：定义整个客服会话的状态机图——
│   │   │   #       状态定义（TypedDict）、节点注册、条件边（路由分支）、
│   │   │   #       interrupt 中断点（转人工）、图的编译与导出
│   │   │   # 可放置：State 类型定义、StateGraph 构建、checkpointer 配置
│   │   │   # 禁止：在此处实现具体的节点逻辑（通过 import nodes/）
│   │   │
│   │   ├── nodes/                           # LangGraph 节点实现
│   │   │   # 职责：每个文件实现状态图中的一个节点函数，接收 State、
│   │   │   #       返回 State 的部分更新。节点是 LangGraph 的最小执行单元
│   │   │   # 禁止：节点之间互相调用（应由 graph.py 通过边/条件边连接）
│   │   │   │
│   │   │   ├── __init__.py
│   │   │   │
│   │   │   ├── router.py                    # 路由节点（入口）
│   │   │   │   # 职责：接收用户消息，调用 LLM 一次性完成——
│   │   │   │   #       意图识别（知识咨询/工单查询/订单查询/敏感操作）、
│   │   │   │   #       槽位提取（工单号、订单号等关键信息）、
│   │   │   │   #       情绪判断（正常/不满/愤怒）、置信度评分
│   │   │   │   # 禁止：根据意图做分发（那是 graph.py 条件边的职责，节点只负责"识别"）
│   │   │   │
│   │   │   ├── knowledge_agent.py           # 知识问答 Agent 节点
│   │   │   │   # 职责：查询改写（上下文补全指代）→ 调用 retrieval_service
│   │   │   │   #       混合检索 → 置信度判断 → 生成带引用来源的回答 →
│   │   │   │   #       低置信度时标记需转人工
│   │   │   │   # 禁止：直接调用 Milvus/ES（通过 retrieval_service）
│   │   │   │
│   │   │   ├── ticket_agent.py              # 工单 Agent 节点
│   │   │   │   # 职责：槽位检查（信息不全时反问用户澄清，形成多轮循环）
│   │   │   │   #       → 槽位齐全后通过 MCP 调用工单系统工具 →
│   │   │   │   #       将结构化返回结果转化为自然语言回复
│   │   │   │   # 禁止：直接实现 MCP 协议（通过 mcp_service）
│   │   │   │
│   │   │   ├── order_agent.py               # 订单 Agent 节点
│   │   │   │   # 职责：身份校验协调（调度双因子验证）→ MCP 调用订单系统 →
│   │   │   │   #       敏感操作判断（退款/售后 → 强制中断转人工）→ 生成回复
│   │   │   │   # 禁止：直接操作订单表（通过 mcp_service → order_service）
│   │   │   │
│   │   │   └── human_handoff.py             # 转人工节点
│   │   │       # 职责：汇总所有转人工触发条件 →
│   │   │       #       置会话状态为 WAITING_HUMAN →
│   │   │       #       发布 RabbitMQ 通知到坐席工作台 →
│   │   │       #       情绪愤怒时进入 VIP 优先队列标记
│   │   │       # 禁止：实现坐席认领逻辑（那是 human_service 的职责）
│   │   │
│   │   ├── prompts/                         # Prompt 模板目录
│   │   │   # 职责：所有 LLM 调用的 Prompt 模板文件，纯文本格式，
│   │   │   #       与代码分离以支持非开发人员调优
│   │   │   # 可放置：.txt 或 .jinja2 模板文件，含占位符变量
│   │   │   # 禁止：在模板中硬编码业务规则（业务规则应在代码中通过逻辑控制）
│   │   │   │
│   │   │   ├── router.txt                   # 意图识别 Prompt
│   │   │   ├── knowledge.txt                # 知识问答 Prompt（含引用要求）
│   │   │   └── ticket.txt                   # 工单处理 Prompt
│   │   │
│   │   └── deep_agent/                      # DeepAgent 自主规划子模块
│   │       # 职责：与 LangGraph 结构化编排并行的一条独立技术路线——
│   │       #       面向开放式任务（分析报告生成、知识库巡检等），
│   │       #       Agent 自主拆解任务步骤，不需预设状态图
│   │       # 禁止：与 LangGraph 节点互相调用（两者场景边界明确，不应耦合）
│   │       │
│   │       └── planner.py                   # DeepAgent 规划器
│   │           # 职责：接收自然语言任务目标 → 自主分解为多步执行计划 →
│   │           #       逐步执行（查询 MySQL → 检索知识库 → 综合生成）→
│   │           #       中间产出实时写 MongoDB → 工具调用次数/token 上限熔断
│   │           # 禁止：处理实时客服对话（那是 LangGraph 主链路的职责）
│   │
│   │
│   ├── mcp_servers/                         # 独立 MCP Server 实现
│   │   # 职责：标准 MCP 协议的 Server 端完整实现——
│   │   #       每个 Server 是一个独立进程/子应用，封装对一类外部系统的操作。
│   │   #       这是 Agent "行动能力" 的物理载体
│   │   # 可放置：MCP Server 初始化和生命周期管理、MCP Tool 的业务实现、
│   │   #       参数校验、返回结构规范
│   │   # 禁止：包含 Agent 编排逻辑（Agent 是 MCP Server 的调用方）、
│   │   #       直接操作不属于自己管辖范围的数据库表
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── ticket_server.py                 # 工单 MCP Server
│   │   │   # 职责：暴露 get_ticket_status / create_ticket / update_ticket
│   │   │   #       三个 MCP 工具。代理请求到 Mock 工单系统的业务逻辑，
│   │   │   #       强制 tenant_id 过滤，写操作携带 client_request_id 幂等键
│   │   │   # 禁止：直接操作数据库连接（通过 ticket_service 间接操作）
│   │   │
│   │   └── order_server.py                  # 订单 MCP Server
│   │       # 职责：暴露 query_order / request_refund 两个 MCP 工具。
│   │       #       代理请求到 Mock 订单系统，强制双因子身份校验，
│   │       #       退款金额系统级上限校验
│   │       # 禁止：直接修改订单表状态（通过 order_service 的状态机规则）
│   │
│   │
│   ├── tasks/                               # Celery 异步任务模块
│   │   # 职责：所有需要异步执行的耗时操作或定时任务。
│   │   #       Worker 进程独立于 Web 服务进程运行
│   │   # 可放置：Celery @app.task 装饰的函数
│   │   # 禁止：在此处处理 HTTP 请求、导入 FastAPI 相关模块
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── knowledge_tasks.py               # 知识库异步任务
│   │   │   # 职责：消费 RabbitMQ knowledge.parse 队列 →
│   │   │   #       调用 RagFlow 解析文档 → 质量检测（乱码率/空内容率）→
│   │   │   #       GPU 服务器 bge-m3 向量化 → 并行写入 Milvus + ES →
│   │   │   #       Milvus/ES 双写不一致重试（指数退避，最多5次）→
│   │   │   #       更新文档状态为已就绪/待人工复核/索引异常 →
│   │   │   #       幂等消费（检查文档当前状态，避免重复处理）
│   │   │   # 禁止：在此处处理 HTTP 文件上传（那是 API 层的职责，任务只处理已入库的文档记录）
│   │   │
│   │   ├── billing_tasks.py                 # 计费定时任务
│   │   │   # 职责：每日定时将 Redis 配额计数器聚合落库至 usage_records →
│   │   │   #       月初自动生成上月账单 → 逾期未付自动降级租户状态
│   │   │   # 禁止：在此处处理实时配额检查（那是 billing_service 同步完成的）
│   │   │
│   │   ├── notification_tasks.py            # 通知异步任务
│   │   │   # 职责：发送邮件（配额预警、文档到期提醒、账单生成通知）→
│   │   │   #       发送短信验证码（调用阿里云短信 SDK）→
│   │   │   #       WebSocket 推送（坐席工作台新会话提醒）
│   │   │   # 禁止：在此处判断"是否应该发送通知"（由各 Service 决定后投递任务）
│   │   │
│   │   └── cleanup_tasks.py                 # 定期清理任务
│   │       # 职责：MongoDB session_traces 过期归档/删除（默认保留90天）→
│   │       #       过期验证码清理 → 僵尸会话（长时间无活动的 WAITING_HUMAN）标记关闭 →
│   │       #       已注销租户数据的定时物理删除（窗口期后）
│   │       # 禁止：清理正在使用中的数据
│   │
│   │
│   ├── core/                                # 核心基础设施层
│   │   # 职责：提供与业务无关的通用技术能力，是系统的"技术底座"。
│   │   #       所有模块都可依赖此层，但此层不依赖任何业务模块
│   │   # 可放置：安全工具、Redis/RabbitMQ 连接管理、限流器、异常定义、通用工具
│   │   # 禁止：import models/、schemas/、crud/、services/、agents/ 中的任何模块
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── security.py                      # 安全工具集
│   │   │   # 职责：bcrypt 密码哈希与校验、JWT 生成与解码（含过期与刷新）、
│   │   │   #       AES-256 对称加解密（PII 字段静态加密）、
│   │   │   #       签名校验（支付回调）、CSRF Token 生成与校验、
│   │   │   #       随机 Token/密钥生成
│   │   │   # 禁止：在此处做权限判断（那是 deps.py 或 auth_service 的职责）
│   │   │
│   │   ├── redis.py                         # Redis 工具集
│   │   │   # 职责：Redis 连接池创建与管理、分布式锁（context manager）、
│   │   │   #       令牌桶限流器基础实现、通用缓存装饰器
│   │   │   # 可放置：Redis 客户端封装、Lua 脚本（原子操作）
│   │   │   # 禁止：在此处定义具体的业务 Key 命名规则（各 Service 使用方自行管理 Key 模式）
│   │   │
│   │   ├── rabbitmq.py                      # RabbitMQ 工具集
│   │   │   # 职责：RabbitMQ 连接管理、Exchange/Queue 声明、
│   │   │   #       消息发布与消费的基础封装、消息确认与重试策略
│   │   │   # 禁止：在此处定义具体的队列名称和路由键（在 tasks/ 模块或 config.py 中定义）
│   │   │
│   │   ├── limiter.py                       # 限流器基础实现
│   │   │   # 职责：令牌桶 / 滑动窗口算法的基础实现，
│   │   │   #       供 rate_limit_service 和 rate_limit_middleware 调用
│   │   │   # 禁止：包含业务维度的限流策略（如"试用版每分钟 10 次"）
│   │   │
│   │   ├── idempotency.py                   # 幂等基础工具
│   │   │   # 职责：幂等键的 Redis 读写封装——写入键+结果缓存、
│   │   │   #       查询键是否存在、设置 TTL
│   │   │   # 禁止：定义幂等键的业务命名规则
│   │   │
│   │   ├── exceptions.py                    # 业务异常体系
│   │   │   # 职责：定义所有自定义异常类（按领域分层）——
│   │   │   #       AppException（基类）→ AuthenticationError、
│   │   │   #       AuthorizationError、TenantNotFoundError、
│   │   │   #       QuotaExceededError、OrderStateInvalidError 等。
│   │   │   #       全局异常处理器（将异常转换为统一 ApiResponse 格式）
│   │   │   # 禁止：在异常类中包含业务逻辑
│   │   │
│   │   └── pagination.py                    # 分页工具
│   │       # 职责：分页参数解析、SQLAlchemy 查询分页封装、
│   │       #       分页结果统一格式化（total/page/page_size/items）
│   │       # 禁止：耦合特定业务模型
│   │
│   │
│   └── utils/                               # 通用工具函数
│       # 职责：与业务无关的纯工具函数，无副作用，可被任意模块安全调用
│       # 可放置：加密解密、Token 计数、格式校验、文件处理、日志配置
│       # 禁止：依赖任何业务模块（models/schemas/services/crud）、
│       #       有外部副作用（如发网络请求、写数据库）
│       │
│       ├── __init__.py
│       │
│       ├── encryption.py                    # PII 加解密工具
│       │   # 职责：手机号、邮箱等敏感字段的 AES-256 加密/解密函数，
│       │   #       密钥通过环境变量注入，不与代码同库
│       │   # 禁止：硬编码密钥或 IV
│       │
│       ├── tokenizer.py                     # Token 计数工具
│       │   # 职责：估算文本的 Token 数量（用于上下文截断预算判断），
│       │   #       支持主流模型的 tokenizer
│       │   # 禁止：在此处做截断决策（那是 agent_service 的职责）
│       │
│       ├── validators.py                    # 参数校验工具
│       │   # 职责：金额格式（正数、最多2位小数）、手机号格式、
│       │   #       身份证号格式、ID 格式、密码强度检测（长度/字符类型/
│       │   #       连续字符/常见弱密码）、URL 格式
│       │   # 禁止：包含数据库查询（如需查重，由调用方处理）
│       │
│       ├── file_parser.py                   # 文件处理工具
│       │   # 职责：文件类型检测（MIME）、文件大小校验、安全文件名生成
│       │   # 禁止：实现文档解析逻辑（那是 RagFlow 的职责）
│       │
│       └── logger.py                        # 日志配置
│           # 职责：结构化日志（JSON 格式）配置、日志级别管理、
│           #       trace_id 自动注入日志上下文、PII 字段自动脱敏
│           # 禁止：记录明文敏感信息
│
│
├── alembic/                                 # 数据库迁移管理
│   # 职责：管理 MySQL 表结构的版本化变更，确保所有环境 Schema 一致
│   # 可放置：Alembic 生成的迁移脚本、env.py 配置
│   # 禁止：手动修改已应用的迁移脚本（应新建迁移）
│   │
│   ├── env.py                               # Alembic 环境配置
│   │   # 职责：配置数据库连接（从 config.py 读取）、
│   │   #       自动发现所有 ORM 模型的 Metadata
│   │   # 禁止：硬编码数据库连接字符串
│   │
│   └── versions/                            # 迁移脚本版本目录
│       # 职责：按时间顺序存放所有数据库变更脚本
│       # 禁止：删除已应用的迁移文件（只能新增）
│
│
├── tests/                                   # 测试套件
│   # 职责：全部自动化测试代码，与业务代码物理分离
│   # 可放置：pytest 测试文件、fixtures、conftest、测试数据
│   # 禁止：将测试代码混入 app/ 业务目录
│   │
│   ├── conftest.py                          # 全局测试配置
│   │   # 职责：pytest 全局 fixture——测试数据库创建/销毁、
│   │   #       测试客户端（TestClient）、Mock 外部服务
│   │   # 禁止：连接生产数据库
│   │
│   ├── unit/                                # 单元测试
│   │   # 职责：测试单个函数/类的逻辑正确性，隔离所有外部依赖
│   │   # 可放置：Service 层单测、CRUD 层单测、工具函数单测
│   │   # 禁止：在此处启动真实数据库/MQ/Redis（应 mock）
│   │
│   ├── integration/                         # 集成测试
│   │   # 职责：测试多个模块协作的正确性，连接测试环境的真实基础设施
│   │   # 可放置：API 端到端测试、Agent 链路集成测试、
│   │   #       MCP Server 集成测试、数据库迁移测试
│   │   # 禁止：连接生产环境
│   │
│   └── fixtures/                            # 测试数据
│       # 职责：存放测试用的 JSON/CSV/SQL 种子数据
│       # 禁止：包含真实 PII 数据
│
│
└── deploy/                                  # 部署相关配置
    # 职责：容器编排、Nginx 配置、环境变量模板等部署相关文件
    # 注意：此目录可能位于项目 Monorepo 根目录而非 backend/ 下，
    #       此处仅标注后端专属的部署文件
```

---

## 3. 关键架构决策与边界

### 3.1 为什么 models/ 和 schemas/ 必须分离

| | `models/`（ORM 物理模型） | `schemas/`（Pydantic 传输模型） |
|---|---|---|
| **关注点** | 数据库表结构映射 | API 协议契约 |
| **变更频率** | 低，跟随 Schema 迁移 | 高，随前端需求变化 |
| **依赖方向** | 不依赖 schemas | 不依赖 models（手动映射字段） |
| **典型场景** | `user.password_hash` | `LoginRequest.phone + password` |

两者不可互相继承。一个字段在 API 层叫 `phone`，在 DB 层可能叫 `encrypted_phone` —— 它们不是同一件事。

### 3.2 为什么 Service 不能直接调 Service（避免循环依赖）

若 `session_service` 需要触发配额计数，它不应直接 `import billing_service`，而是通过以下方式之一：

- **事件总线**：`session_service` 发出 `SessionResolved` 事件，`billing_service` 订阅该事件
- **上层编排**：由 `agent_service`（上游调用方）在调用完 `session_service` 后再调 `billing_service`

### 3.3 Agent 层与 MCP 层的隔离

```
Agent（agents/nodes/order_agent.py）
    │  "我需要查订单状态"
    ▼
MCP Service（services/mcp_service.py）
    │  权限矩阵校验 → 幂等检查 → 入参校验 → 熔断判断
    ▼
MCP Server（mcp_servers/order_server.py）
    │  标准 MCP 协议实现
    ▼
Order Service（services/order_service.py）
    │  状态机校验 + 乐观锁
    ▼
Order CRUD（crud/order.py）→ MySQL
```

Agent 永远不越过 MCP Service 直接调用 MCP Server，更不直接调用业务 Service。这样未来把 Mock 工单系统换成真实客户系统时，只换 MCP Server 层实现即可。

### 3.4 异步任务与 API 层的隔离

**API 层 → RabbitMQ → Celery Worker → 外部系统**

API 层只负责 **投递任务消息**，不等待结果。Worker 消费消息并处理，完成后更新数据库状态，前端通过轮询或 WebSocket 感知完成。这种隔离确保：
- 文档上传接口 200ms 内返回（仅写 MySQL 元数据 + 投递消息）
- 大文档解析 3 分钟在后台静默完成
- Worker 挂了不影响 Web 服务接受新请求

---

## 4. 项目命名规约

| 维度 | 规约 | 示例 |
|---|---|---|
| **文件名** | snake_case | `order_service.py` |
| **类名** | PascalCase | `OrderService`、`OrderStateMachine` |
| **函数/变量** | snake_case | `get_order_by_id()` |
| **常量** | UPPER_SNAKE_CASE | `MAX_RETRY_COUNT` |
| **数据库表** | 复数 snake_case | `billing_invoices` |
| **API 路径** | kebab-case（资源复数） | `/api/v1/knowledge-documents` |
| **Redis Key** | 冒号分隔 snake_case | `quota:{tenant_id}:{yyyymm}` |
| **RabbitMQ Queue** | 点分隔 snake_case | `knowledge.parse.{tenant_id}` |

---

*本文档仅定义架构与目录结构规约，不涉及具体技术实现。开发时请对照此文档检查每个新增文件的归属是否符合层级职责边界。*
