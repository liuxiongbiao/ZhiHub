# ZhiHub 电商演示环境指南（P0-3）

本指南说明如何创建、重置和使用「ZhiHub 智选商城」演示租户——一个开箱即用的电商客服演示环境，
无需手工上传知识或创建订单。

## 环境限制

演示 seed **仅允许在 `development` / `test` / `demo` 环境执行**，`production` 环境会直接拒绝。
演示租户数据与其他租户完全隔离，seed/reset 只操作演示租户自己的数据；
演示订单号（`ORD-DEMO-*`）若被其他租户占用，seed 会报错退出，绝不修改他人数据。

## 一、创建演示环境

在 backend 目录执行（或进入 backend 容器执行）：

```bash
python -m app.demo.demo_seed create
```

幂等：可重复执行，不会创建重复租户/用户/订单。

### 演示账号密码

演示密码**不在源码中硬编码**，按以下优先级提供：

1. 命令行参数：`--admin-password` / `--agent-password`
2. 环境变量：`DEMO_ADMIN_PASSWORD` / `DEMO_AGENT_PASSWORD`
3. 都不提供时：自动随机生成并在**本次输出中显示一次**（不落盘，请在演示前记下）

示例：

```bash
python -m app.demo.demo_seed create --admin-password '你的管理员密码' --agent-password '你的客服密码'
```

密码需满足系统强度要求（≥8 位、含字母与数字等）。

### 演示账号

| 身份 | 手机号 | 说明 |
|---|---|---|
| 演示管理员 | 13900000000 | 后台管理、Widget 配置 |
| 演示客服 1 | 13900000001 | 坐席工作台 |
| 演示客服 2 | 13900000002 | 坐席工作台 |
| 终端客户 | 匿名 Widget | 无后台账号；订单绑定手机号 13911112222（后四位 2222，用于双因子校验） |

## 二、重置演示环境

```bash
python -m app.demo.demo_seed reset --tenant-id <演示租户ID> --yes
```

- 必须显式提供 `--tenant-id` 且该 ID 必须是 **active** 演示租户（`is_demo` 标记 + active 状态），
  否则拒绝执行；对已归档（closed）实例再次 reset 会无修改地返回 `already_archived`
- reset 采用**归档**语义，不是删除：
  - 演示租户被关闭并改名（`ZhiHub 智选商城（已归档 <时间戳>）`），成为一条 archived tenant 记录；
    固定演示手机号被改为 199 段 11 位占位号（合法、全局唯一），演示订单/工单号改为归档号，
    从而释放给下一个演示实例复用
  - **不可变审计历史全部保留**：`refund_approval_events`、审计日志等逐字段原样保留，
    reset 前后可逐字段比对验证
  - 全部数据库归档操作在**单个事务**内完成，任一步失败整体回滚，不会出现半归档状态
  - 归档提交后再做外部清理（上传文件 / Milvus / ES 切片），失败单独报告 `partial`
- 只清理演示租户自己的业务数据（订单/工单/退款/会话/知识/账单/通知/审计 + 向量库切片），
  不做全库清理；混合归属（同时属于非 demo 租户）的账号不会被释放手机号
- create / reset 的输出会报告全库演示实例数量（`demo_tenants_active` /
  `demo_tenants_archived`）——活动实例不累积，但 archived 实例会随每次 reset 增加一条
- 重置后可再次 `create` 恢复全套演示数据（全新实例、新 tenant_id）

### 认领 d34 迁移前的旧演示实例

`tenants.is_demo` 列（d34 迁移）引入之前创建的旧演示实例不会被普通 `create` 自动认领
（避免误认领同名普通租户）。如需认领，显式传参：

```bash
python -m app.demo.demo_seed create --claim-legacy ...
```

认领判据（全部满足才认领）：租户同名且 active、3 个固定演示手机号都是该租户
精确角色的成员、且这些账号不存在任何非 demo 租户的混合归属。

## 三、演示剧本

真实口径：**客户提出退款/赔偿 → 系统转人工 → 客服核验 → 客服通过正式端点创建退款/工单**
（退款与赔偿属敏感操作，系统设计上直接转人工，不会自动创建申请）。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 打开嵌入 Widget 的客户页面 | 匿名会话建立 |
| 2 | 询问「你们支持七天无理由退货吗？退货流程是什么？」 | AI 从知识库回答并显示「引用: 七天无理由退货政策.md」 |
| 3 | 询问「帮我查一下订单 ORD-DEMO-0003，我的手机号后四位是 2222」 | 系统调用订单工具，返回真实订单（金额 899 元、已发货、物流单号 SF1234567890） |
| 4 | 询问「我收到的商品运输中破损了，你们能额外赔偿我 500 元吗？」 | 知识库无明确答案 -> 转人工（waiting_human） |
| 5 | 客服 13900000001 登录坐席工作台，按会话 ID 认领并回复 | 客户 Widget 实时收到人工消息 |
| 6 | 客服核验后通过正式端点代客户创建退款（POST /api/v1/order/refunds/request，双因子+幂等）或创建工单 | 退款审批/工单真实创建 |
| 7 | 管理员查看会话列表/账单 | 本次会话与用量可统计（Dashboard 后续批次） |

预置数据速览：6 个订单（待支付/已支付可发货/已发货含物流/已完成/可退款/退款审批中）、
2 个工单、2 笔退款（活动超额待管理员终审 + 终态驳回示例，均带完整事件流水）、
waiting_human 与 closed 会话、快捷回复、账单与用量、通知与审计样例。

## 四、服务地址（本机默认）

| 服务 | 地址 |
|---|---|
| 管理后台 / 客户站点（nginx） | http://localhost |
| 后端 API | http://localhost:8000/api/v1 |
| Widget 脚本 | http://localhost/widget.js |
| 嵌入 Widget 的外部客户页示例 | 由 e2e 脚本或任意白名单域名页面加载 widget.js |

## 五、浏览器 E2E

```bash
cd frontend
E2E_BASE=http://localhost E2E_API=http://localhost:8000 node tests/e2e/e2e-p03-demo.cjs
```

- E2E 开始前自动 reset→create 建立干净基线；退款/工单使用固定幂等键
  （`client_request_id`），重复运行不累积业务数据；同一运行内重复提交相同幂等键
  会断言返回同一审批/工单且总数只增加一次
- session_id 从 `POST /api/v1/widget/session` 的实际响应捕获，认领/回复/统计
  均断言该精确会话，不取 waiting 列表第一条
- 订单查询口径：客户提供**手机号后四位 2222**（与订单绑定手机号 13911112222
  匹配做双因子校验）。order agent 原生接受后四位或完整 11 位手机号，
  E2E 无"补发完整号码"兜底，一轮内必须返回金额/状态/物流单号
- 验收 A（同一 active tenant 连续运行两次不重复）：第一次正常运行后，第二次加
  `E2E_NO_RESET=1` 运行（跳过开场 reset，只幂等补齐）
- 验收 B（reset 保留不可变审计历史）：

```bash
node tests/e2e/e2e-p032-reset-verify.cjs
```

  reset 一次后创建新实例，脚本会对旧租户的 `audit_logs` 与
  `refund_approval_events` 做逐字段前后比对，并报告全库演示实例 active/archived 数量。

> 说明：本文档不包含任何真实密钥或真实密码；演示密码由使用者自行设置（见上文）。
