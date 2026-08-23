# P1/P2 遗留项规格草案（backlog-specs）

> 生成日期：2026-08-12 | 状态：草案（未实施）
> 说明：以下各项按“验收条件/边界/非功能约束”给出最小规格，供审批后进入 TDD 实施。

## B-01 退款审批只追加（P1-1）

> 状态：✅ 已实施（2026-08-12，迁移 d25 + test_refund_outbox.py）

- 目标：初审（agent 大额）与终审（admin）必须各自生成一条审批记录，禁止 UPDATE 已有审批结论。
- 验收：`refund_approvals` 新增 `review_history` JSON 列（或独立明细表）；两次审批产生两条不可变记录；
  审计查询可按时间还原完整审批链。
- 边界：终审引用初审记录 ID；拒绝/撤销同样追加。

## B-02 退款对账与 Outbox（P1-2）

> 状态：✅ 已实施（2026-08-12，迁移 d25 + test_refund_outbox.py；
> 外部失败自动重试，资金已动+本地 CAS 冲突置人工复核，不重复退款）

- 目标：外部退款调用与 DB 事务解耦，杜绝“钱已退、状态未变”的账务不一致。
- 验收：新增 `refund_outbox` 表（pending/success/failed + 幂等键）；审批通过后先落 outbox 再提交事务；
  独立 Worker 轮询执行支付宝退款并回写状态；失败重试 + 人工告警。
- 非功能：至少一次语义，幂等键防重复退款；失败必须可观测。

## B-03 定时任务迁移 Celery Beat（P1-3）

- 目标：移除 web 进程内计费/清理线程，改为独立 Worker + Beat，多副本下仅一个实例执行。
- 验收：`docker-compose` 新增 celery-worker / celery-beat 服务；`main.py` 不再启动调度线程；
  关键任务执行前抢 Redis 分布式锁（TTL 覆盖任务最长执行时间）。

## B-04 加密密钥版本化（P1-4）

- 目标：支持密钥轮换而无需重加密存量数据。
- 验收：`encrypted_fields` 增加 `key_version` 列；新写用当前密钥，读取按版本选择密钥；
  提供 `rotate_encryption_key` 迁移命令；KMS 对接为可插拔实现。

## B-05 降级策略配置化（P1-6）

- 目标：Redis 故障时鉴权黑名单/限流的 fail-open/fail-closed 由配置控制。
- 验收：`AUTH_FAIL_OPEN`、`RATE_LIMIT_FAIL_OPEN` 环境变量；管理/资金接口默认 fail-closed；
  配置变更无需改代码。

## B-06 可信代理与真实 IP（P1-7）

- 目标：反代后登录限流/审计基于真实客户端 IP。
- 验收：uvicorn `--proxy-headers` + `TRUSTED_PROXY_CIDRS` 配置；仅可信网段来源的
  `X-Forwarded-For` 被采用；测试覆盖伪造头场景。

## B-07 tiktoken 离线化（P1-13）

- 目标：内网/离线部署不依赖运行时下载 BPE 词表。
- 验收：Dockerfile 构建期预热 `tiktoken` 缓存目录并复制进镜像；提供离线词表校验和；
  无外网环境冒烟通过 token 计数。

## B-08 用量明细导出（P1-15）

- 目标：导出 PRD 3.9 要求的会话级明细（会话 ID/时间/意图/模型/输入输出 Token/是否超额）。
- 验收：新增 `usage_detail` 表（会话粒度）；`GET /billing/usage/export?type=detail` 输出 CSV；
  与账单金额可逐条对账。

## B-09 MilvusClient 迁移（P1-17）

- 目标：消除 `pymilvus` ORM API 弃用告警。
- 验收：`embedding_client.py` 全部使用 `MilvusClient`；单元/集成测试覆盖建集、插入、检索、删除；
  无 DeprecationWarning。

## B-10 前端分包与令牌存储（P2-1/P2-2）

- 目标：主包 <500KB（gzip）；JWT 不落 localStorage。
- 验收：`manualChunks` 拆分 element-plus/axios；构建产物主 chunk gzip <500KB；
  登录态改 HttpOnly Cookie + CSRF 双提交，或短期内存令牌 + refresh 接口。

## B-11 WebSocket 安全基线（P2-3）

- 验收：生产强制 `wss`；服务端校验 `Origin` 白名单；access log 对 query token 脱敏；
  连接数按租户/用户限流。

## B-12 单一迁移轨（P2-4）

- 验收：`APP_ENV != development` 时 `init_db` 直接报错并提示执行 `alembic upgrade head`；
  CI 增加 `alembic check`。

## B-13 配置中心化（P2-5）

- 验收：套餐配额/基础费/超额价/模型权重/存储配额统一由配置或 DB 驱动；
  运营变更无需发版。

## B-14 会话状态枚举（P2-6）

- 验收：新增 `SessionStatus`/`OrderStatus` 枚举常量模块，全仓库引用同一常量；
  `rg "agent_handling"` 不再出现字符串字面量。

## B-15 specs 归档治理（P2-7）

- 验收：有效 SDD 移入 `docs/specs/`，过程审查文档移入 `archive/`；
  根目录仅保留索引文件；所有文档不再含“面试/10天/演示”字样。
