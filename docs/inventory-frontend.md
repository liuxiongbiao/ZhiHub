# ZhiHub 前端盘点报告（代码级，只读审查）

> 盘点日期：2026-08-17 | 范围：frontend/src（Vue3 + TypeScript + Element Plus + Pinia） | 本轮未修改任何代码

## 1. 路由表完整清单

路由全部集中在 `frontend/src/router/index.ts`（仅此一个文件），`createWebHistory` 模式。API 前缀 `/api/v1`（`frontend/src/constants.ts:4`）。

### 公开路由（meta.public = true，无需登录）

| path | name | 组件 | meta | 行号 |
|---|---|---|---|---|
| `/login` | login | `views/login/Login.vue` | public | 26-31 |
| `/register` | register | `views/login/Register.vue` | public | 32-37 |
| `/activate-email` | activate-email | `views/login/ActivateEmail.vue` | public | 38-43 |
| `/accept-invite` | accept-invite | `views/onboarding/AcceptInvite.vue` | public | 44-49 |

### `/` 下（AdminLayout 包裹，全部需登录）

| path | name | 组件 | requiresRole | 行号 |
|---|---|---|---|---|
| `/` | dashboard | `views/dashboard/Home.vue` | 无（仅登录） | 54-58 |
| `/setup` | setup-wizard | `views/onboarding/SetupWizard.vue` | admin | 60-65 |
| `/settings` | plan-settings | `views/onboarding/PlanSettings.vue` | admin | 66-71 |
| `/onboarding/members` | member-invite | `views/onboarding/MemberInvite.vue` | admin | 72-78 |
| `/widget/config` | widget-config | `views/onboarding/WidgetConfig.vue` | admin | 79-85 |
| `/audit/logs` | audit-logs | `views/audit/AuditLog.vue` | admin | 86-92 |
| `/profile` | profile | `views/profile/Profile.vue` | 无 | 93-98 |
| `/knowledge/list` | knowledge-list | `views/knowledge/KnowledgeList.vue` | admin | 99-104 |
| `/knowledge/search-test` | knowledge-search-test | `views/knowledge/SearchTest.vue` | admin | 105-110 |
| `/ticket/list` | ticket-list | `views/ticket/TicketList.vue` | 无 | 111-115 |
| `/order/list` | order-list | `views/order/OrderList.vue` | 无 | 116-120 |
| `/order/refunds` | order-refunds | `views/order/RefundApproval.vue` | admin, agent | 121-126 |
| `/billing/usage` | billing-usage | `views/billing/UsageOverview.vue` | admin | 127-132 |
| `/billing/invoices` | billing-invoices | `views/billing/InvoiceList.vue` | admin | 133-138 |
| `/session/:id` | session-detail | `views/session/SessionDetail.vue` | 无 | 139-143 |
| `/session/monitor` | session-monitor | `views/session/SessionMonitor.vue` | admin | 144-150 |
| `/agent-report` | agent-report | `views/agent-report/DeepAgentReport.vue` | 无 | 151-155 |
| `/widget/preview` | widget-preview | `views/widget-demo/WidgetPreview.vue` | 无 | 156-161 |

### `/agent` 下（AgentLayout）

| path | name | 组件 | meta | 行号 |
|---|---|---|---|---|
| `/agent/console` | agent-console | `views/agent/AgentConsole.vue` | 无（仅登录，未限角色） | 164-174 |

注意：meta 字段名是 `public` 和 `requiresRole`（无 requiresAuth/roles/title），无 title meta。

## 2. 角色守卫逻辑

守卫在 `frontend/src/permission.ts`（在 `main.ts` 通过 `registerGuard(router)` + `wireUnauthorized(router)` 激活，见 `router/index.ts:20` 注释）：

- 核心决策纯函数 `resolveNavigation(to, auth)`（44-72 行）：
  - 公开路由 + 已登录访问 login/register -> 重定向 `/`（51-53 行）
  - 受保护路由 + 未登录 -> `/login?redirect=原路径`（59-61 行）
  - 已登录 + `requiresRole` 未定义 -> 放行（64-65 行）
  - 已登录 + 角色命中白名单 -> 放行（68 行）
  - 已登录 + 角色不命中 -> `/?forbidden=1`（71 行）
- `registerGuard`（75-86 行）惰性 `import('@/store/modules/user')` 取 `isLoggedIn`/`role`（避免循环依赖）。角色来源是 localStorage `zhihub:user` 持久化的 role（`store/modules/user.ts:53-54, 112-124`），role 类型只有 `'admin' | 'agent'`（user.ts:31）——**前端没有"普通用户/最终客户"角色**。
- 401 处理链：`api/request.ts` 响应拦截器 401 -> 先尝试 `POST /auth/refresh` 换 token 重放一次（request.ts:116-131），refresh 失败 -> 清 store -> 跳 `/login`（permission.ts:89-99 `wireUnauthorized`）。
- **登录成功后无按角色跳转**：`Login.vue` 的 `safeRedirect()`（120-124 行）只跳 `?redirect` 参数（防 `//` 开放跳转），否则一律跳 `/`。agent 登录后也落在 AdminLayout 的 dashboard。唯一例外：接受邀请成功后固定跳 `/agent/console`（`AcceptInvite.vue:12`）。

## 3. 侧边栏/菜单

- **AdminLayout**（`frontend/src/layouts/AdminLayout.vue:8-108`）：菜单写死在模板，用 `v-if="userStore.role === 'admin'"` 按角色显隐（非路由驱动过滤）。
  - admin 可见全部：工作台(/)、Widget 预览(/widget/preview)、品牌定制、知识管理(知识库/检索测试台)、工单与订单(工单看板/会话监控/订单列表/退款审批)、计费(用量概览/账单列表)、智能体(DeepAgent 报告/坐席工作台)、配置向导、规则配置、审计日志。
  - agent 可见：工作台、Widget 预览、工单看板、订单列表、退款审批、智能体(DeepAgent 报告/坐席工作台)。
  - 顶栏含租户名+套餐标签、站内信铃铛（`api/notification.ts`，AdminLayout.vue:120-134）、用户下拉（个人中心/退出）。
- **AgentLayout**（`frontend/src/layouts/AgentLayout.vue:7-17`）：深色固定菜单 3 项——会话处理(/agent/console)、工单看板、退款审批。无角色过滤。
- 菜单与路由 meta 有轻微不一致：AgentLayout 的"工单看板/退款审批"点击后实际会导航到 AdminLayout 下的 `/ticket/list`、`/order/refunds`（换布局）。

## 4. 页面清单（frontend/src/views，共 23 个）

| 组件 | 功能 |
|---|---|
| `login/Login.vue` | 登录页，4 个 tab：账号密码/邮箱/短信验证码/微信扫码（Login.vue:5-63，微信回调 onMounted 195-219） |
| `login/Register.vue` | 注册页，手机号（短信验证码+企业名）与邮箱两种注册 |
| `login/ActivateEmail.vue` | 公开邮箱激活落点，展示处理中/成功/过期/失败状态 |
| `onboarding/AcceptInvite.vue` | 公开邀请落点，匿名先登录带回、已登录确认加入租户，成功跳坐席工作台 |
| `onboarding/SetupWizard.vue` | 配置向导，5 步状态机 basic->knowledge->mcp->team->done |
| `onboarding/PlanSettings.vue` | 规则配置：大额退款审批阈值设置 + 租户数据 ZIP 导出 |
| `onboarding/MemberInvite.vue` | admin 输坐席手机号生成邀请链接 |
| `onboarding/WidgetConfig.vue` | Widget 品牌定制（主题色/Logo/欢迎语）+ 获取嵌入 script 代码（经 `GET /tenant/widget/embed-code`）。**注意**：后端已有 `/tenant/widget/api-key` 与 `/tenant/widget/domains` 端点，但该配置页未接入（不能在此轮换 API Key 或维护域名白名单） |
| `audit/AuditLog.vue` | admin 审计日志分页表格（时间/操作/对象/操作人） |
| `profile/Profile.vue` | 个人中心：验证码改密、绑定邮箱、绑定微信 |
| `dashboard/Home.vue` | 管理员首页（见下详述） |
| `knowledge/KnowledgeList.vue` | 知识库文档管理：上传(pdf/docx/md/xlsx+有效期)、状态筛选、列表 |
| `knowledge/SearchTest.vue` | 检索测试台：纯向量 vs 混合检索结果对比 |
| `ticket/TicketList.vue` | 工单看板：按状态 tab 分栏 + 详情抽屉 |
| `order/OrderList.vue` | 订单列表：状态分栏、新建订单、发货（填物流单号） |
| `order/RefundApproval.vue` | 退款审批列表：规则提示 + 批准/拒绝/撤销 |
| `billing/UsageOverview.vue` | 用量概览：配额进度条 + 80%/100% 预警 + 用量明细 CSV 导出 |
| `billing/InvoiceList.vue` | 账单列表：基础费/超额费/合计 + 支付宝跳转支付 |
| `session/SessionMonitor.vue` | admin 会话监控：按状态/情绪筛选的会话列表，点击进详情 |
| `session/SessionDetail.vue` | 会话详情：tab 切换对话记录（含来源引用+反馈按钮）与 Agent trace 时间线 |
| `agent-report/DeepAgentReport.vue` | DeepAgent 报告：输入目标 -> POST /agent/deep-task -> 步骤时间线 + markdown 报告下载 + 历史任务查询（148、172 行直连 request） |
| `agent/AgentConsole.vue` | 坐席工作台（见下详述） |
| `widget-demo/WidgetPreview.vue` | 终端用户对话 demo 页（见下详述） |

**管理员首页 dashboard（`views/dashboard/Home.vue`）**：不是数据大盘，没有图表/统计数字。内容为：① 三个横幅——系统降级告警、配额预警、配置向导未完成提示（6-23 行）；② 租户信息描述表（企业名/套餐/状态/当前角色，27-38 行）；③ 快捷入口卡片（知识库/工单看板/订单列表/成员邀请，按角色过滤 adminOnly，75-80 行）。接口：`GET /tenant/home`（经 tenantStore.fetchHome，见 `api/tenant.ts:29-32`）+ `GET /tenant/wizard`（Home.vue:89，仅 admin 判定向导完成度）。

**客服工作台（`views/agent/AgentConsole.vue`，路由 `/agent/console`）**：两栏而非三栏——左栏"等待人工"会话队列表格（会话ID/状态/意图/输入中标记/认领/退回AI 按钮，7-25 行），右栏选中会话后上下分为 ChatWindow（agent 模式 + 快捷回复条，31-46 行）和 Agent 执行轨迹 TraceTimeline（49-52 行）。**没有独立的客户信息面板**。实时性靠 WebSocket：`POST /auth/ws-token` 换短效 token -> `ws /api/v1/ws/agent`（87-118 行），处理 new_session / session_claimed / typing 事件。

**面向最终客户的聊天入口（Widget，判定：部分已有）**：

- **Vue 公开聊天页：不存在**。`/widget/preview`（WidgetPreview.vue）是唯一的终端用户对话页面，但它是**登录后** AdminLayout 内的 demo 页（路由无 meta.public，guard 要求登录），调用 `POST /session` 创建会话 + ChatWindow 聊天 + 弹窗提交工单（`POST /ticket`）。
- **真实 Widget SDK 已存在**：`frontend/public/widget.js`（自托管，经 nginx 静态服务，可通过 `/widget.js` 访问）。用法 `<script src="/widget.js" data-api-key="..." data-endpoint="/api/v1/widget">`。它已能：创建匿名会话（`POST /api/v1/widget/session`，携 API Key）-> 用返回的 Widget JWT 调 `POST /api/v1/widget/{session_id}/message` 发消息并显示 AI 回复。
- **SDK 当前缺口**：
  1. **未连接** `WS /api/v1/ws/session/{session_id}`--后端下行通道已有，但 SDK 收不到 `agent_message`，转人工后坐席回复无法到达客户；
  2. 无人工接入状态提示（waiting_human/human_handling 无 UI 表达）；
  3. 不展示回答引用（sources）、不支持 CSAT 评价；
  4. 无工单/订单/退款状态展示。
- **嵌入代码限制**：当前 embed code（`GET /tenant/widget/embed-code` 生成）使用 `/widget.js` 和 `/api/v1/widget` **相对地址**，粘贴到外部网站不能直接工作（需改为绝对地址/可配置 endpoint 才可跨站使用）。

## 5. frontend/src/api 接口路径清单

baseURL `/api/v1`（request.ts:59-62）；request.ts:98-106 注入 `Authorization: Bearer` + `X-CSRF-Token`。

**auth.ts**：POST /auth/register、/auth/login、/auth/logout、/auth/ws-token、/auth/sms/send、/auth/login/sms、/auth/register/email、/auth/login/email、/auth/email/activate、/auth/password/reset、/auth/bind/email、/auth/bind/phone、/auth/wechat/bind；GET /auth/wechat/auth、/auth/wechat/callback；POST /auth/code/send。（另 request.ts:82 POST /auth/refresh）

**session.ts**：POST /session、/session/message、/session/message/stream(SSE fetch)、/session/{id}/agent-message、/session/{id}/claim、/session/{id}/return-to-agent、/session/{id}/leave-message、/session/{id}/csat、/session/quick-replies、/session/message/{id}/feedback；GET /session/list、/session/{id}、/session/{id}/trace、/session/quick-replies。

**knowledge.ts**：POST /knowledge/upload(multipart)、/knowledge/{id}/rebuild、/knowledge/test；GET /knowledge/list；DELETE /knowledge/{id}。

**ticket.ts**：GET /ticket/list、/ticket/{id}；POST /ticket。

**order.ts**：GET /order/list、/order/{id}、/order/refunds/list、/order/refunds/rules；POST /order、/order/refunds/{id}/approve、/order/refunds/{id}/cancel、/order/{id}/ship。

**billing.ts**：GET /billing/quota、/billing/invoices、/billing/usage、/billing/usage/export(blob)、/billing/usage/export-detail(blob)；POST /billing/pay/{invoiceId}。

**tenant.ts**：GET /tenant/home、/tenant/wizard、/tenant/audit-logs、/tenant/widget/embed-code、/tenant/refund-threshold、/tenant/export(blob)；POST /tenant/wizard/next、/tenant/invite、/tenant/invite/accept、/tenant/widget；PUT /tenant/refund-threshold。

**notification.ts**：GET /notifications、/notifications/unread-count；POST /notifications/{id}/read。

**DeepAgentReport.vue 直连**（未封装进 api/）：POST /agent/deep-task、GET /agent/deep-task/{task_id}。

## 6. stats/analytics/dashboard 与知识缺口/FAQ 审核

- **无**独立的 stats/analytics 页面或 `/stats`、`/analytics` 类接口封装。最接近的：`/billing/usage` 用量概览（单配额数字+进度条）、`session/monitor` 会话列表（含情绪/等待时长列）、`agent-report` DeepAgent 报告（一次性分析报告，非持久 dashboard）。dashboard 路由（`/`）只是租户信息+快捷入口，无统计图表。
- **无**知识缺口（knowledge gap）/FAQ 审核/FAQ 生成相关页面、路由或 API。知识管理只有 KnowledgeList（上传/列表/删除/重建索引）和 SearchTest（检索对比），没有人工审核流。全前端 grep `faq|知识缺口|审核` 仅命中 RefundApproval 的"审核退款"文案。

## 7. 其他值得注意的点

- 角色模型只有 admin/agent 两种，无最终用户角色；`/agent/console` 路由未设 requiresRole，admin 也能进（菜单里 admin 同样可见"坐席工作台"入口）。
- 会话创建/发消息（`POST /session`、`/session/message`）走的是带 Bearer 的认证接口，Widget 场景也是登录用户自己在测——即整条终端用户聊天链路目前托管在管理端内。

## 8. 前端测试现状

- **单测/组件测（vitest）**：配置在 `frontend/vite.config.ts`（`test.include: ['tests/**/*.spec.ts']`，jsdom 环境，setup `tests/setup.ts`）。共 27 个 spec 文件、96 个测试，分布在：`tests/unit/`（15 个：auth-api、billing-api、order-api、session-api、tenant-api、request、ws-client、各 store 等）、`tests/components/`（11 个：Login/Register/Home/ChatStream/KnowledgeList/SessionDetail 等）、`tests/integration/auth-flow.spec.ts`。
- **注意：package.json 没有 `npm test` 脚本**，vitest 只能 `npx vitest run` 直接跑。
- **浏览器 E2E**：`tests/e2e/*.cjs` 共 11 个脚本（auth/nav/features/kb/chat/deepagent/api/profile/closure/fresh + e2e-helper），用 **playwright 库**（devDependencies `playwright ^1.49.0`）手写驱动真实浏览器打 `localhost:5173`，每步截图存 `tests/e2e/e2e-shots/`。无 Cypress，无 @playwright/test runner。
- devDependencies：vitest ^2.1.9、@vue/test-utils ^2.4.11、jsdom ^25.0.1、playwright ^1.49.0。

## 9. 模块判定总表（已有 / 部分已有 / 缺失）

| 模块 | 判定 | 依据 |
|---|---|---|
| 角色入口 | **部分已有** | admin/agent 有登录+菜单+守卫（agent 无专属落地页，登录后落 admin 首页）；最终客户有 widget.js SDK 但无公开 Vue 聊天页 |
| 管理员看板 | **缺失** | 首页仅租户信息+快捷入口，无统计图表；无 stats 接口封装（数据源大半已在后端） |
| 客户 Widget | **部分已有** | 已有：API Key、域名白名单、Widget JWT、匿名消息 API（widget.js 可用）、终端用户 WS 后端；缺失：SDK 未连 WS（收不到 agent_message）、无人工接入状态/引用/CSAT/工单订单退款展示、embed code 相对地址外站不可直接用 |
| 客服工作台 | **部分已有** | 两栏（会话队列 + 聊天/Trace）+ WS 实时已有；缺第三栏客户上下文面板（查订单/建单/退款） |
| 演示租户 | **缺失** | seed 全是 SaaS 套餐语义，无电商租户与客户账号 |
| 知识缺口采集 | **部分已有** | 无专用前端页面；但后端 `messages.msg_metadata`（intent/confidence/sentiment/degraded/quota_status）与 `sources` 已落库，可作初步缺口信号 |
| 知识缺口处理 | **缺失** | 无缺口列表页、无聚合分析入口 |
| FAQ 候选审核 | **缺失** | 无候选生成、无审核流页面与状态机 |
