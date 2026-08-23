# 014 Widget 公共通道（widget-public-channel）

> 状态：实施中 | 日期：2026-08-12
> 关联：修复清单 P0-1

## 1. 背景与目标

PRD 3.1.3 要求终端用户**免注册**通过嵌入 Widget 发起对话。此前所有会话接口强制员工 JWT，
客户嵌入后必 401。本规格建立匿名公共通道：

- 租户级 Widget API Key（仅存 SHA-256 哈希，原始 Key 生成时返回一次）。
- 域名白名单（精确/子域匹配；未配置时仅开发环境允许 localhost）。
- 匿名会话 + 短时效 Widget 令牌（默认 30 分钟）。
- 令牌仅可访问 `/api/v1/widget/*`，不可用于员工后台接口。
- 单会话消息限流（10 次/分钟）。

## 2. 接口契约

### POST /api/v1/widget/session（公开）

请求：`{"api_key": "...", "origin": "https://shop.example.com"}`（origin 缺省取 Origin/Referer 头）

响应 201：`{"session_id", "token", "expires_in", "welcome_text"}`

错误：401 API Key 无效；403 域名不在白名单。

### POST /api/v1/widget/{session_id}/message（需 Widget 令牌）

请求：`Authorization: Bearer <widget-token>`，body 同 `/api/v1/session/message`。

错误：403 令牌会话与路径不一致；404 会话不存在；429 超限。

### POST /api/v1/widget/refunds/{approval_id}/cancel（需 Widget 令牌）

请求 body：`{"session_id", "phone"}`。校验会话归属 + 手机号后 4 位匹配订单绑定手机号。

错误：403 令牌不匹配；400 身份校验失败；409 已处理不可撤销。

### 管理端点（admin）

- `POST /api/v1/tenant/widget/api-key`：生成/轮换 Key（旧 Key 立即失效）。
- `PUT /api/v1/tenant/widget/domains`：设置白名单。

## 3. 验收条件

- [ ] 有效 Key + 白名单域名 -> 201 且返回可用的 widget token。
- [ ] 无效 Key -> 401；未授权域名 -> 403。
- [ ] widget token 访问 `/api/v1/session/*` -> 401（通道隔离）。
- [ ] 令牌会话与消息路径不一致 -> 403。
- [ ] 同会话 10 次/分钟后第 11 次 -> 429。
- [ ] Widget 撤单：phone 匹配 -> 200；不匹配 -> 400。
- [ ] `/widget.js` 随前端构建产物发布（同源自托管，无外部 CDN）。

## 4. 边界与错误处理

- API Key 永不落明文库；轮换后旧 Key 立即失效。
- 白名单空 + 非开发环境 -> 全部拒绝（fail-closed）。
- 会话创建不依赖员工登录态；PII 最小化（end_user_id 为随机匿名标识）。

## 5. 非功能约束

- 消息路径复用 Agent 主链路（注入检测/计费/转人工），不另起炉灶。
- 令牌有效期 30 分钟可配置；匿名会话受 Redis 限流保护。
