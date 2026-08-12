# 安全模型说明

> 更新日期：2026-08-12
> 背景：审计发现原 CSRF 中间件只校验请求头“存在”而不校验值，属无效宣称；
> 且该中间件已实际阻断所有未携带 `X-CSRF-Token` 的 Bearer API 客户端（契约测试复现 403）。

## 当前认证与 CSRF 防护模型

1. 认证方式：`Authorization: Bearer <JWT>`，JWT 存放于前端内存/localStorage，**不使用 Cookie 会话**。
2. 跨域策略：后端 CORS 白名单（`CORS_ORIGINS` 配置），仅允许可信来源；浏览器不会自动为跨域请求附加
   `Authorization` 自定义头，因此“跨站请求伪造（CSRF）”在无 Cookie 场景下不构成可利用攻击面。
3. 决策：**移除 CSRF 中间件注册**，以“Bearer 头 + CORS 白名单”作为防护模型，并在此文档化。
4. 约束：若未来引入 Cookie 会话（HttpOnly Cookie 登录态），必须同步引入
   “双提交 Cookie / Signed Double-Submit” 类 CSRF 防护，禁止直接复用旧的“仅检查头存在”实现。

## 纵深防御清单（现状）

- 密码：bcrypt（cost 自适应）+ 失败锁定（手机号/IP 双维度）
- 令牌：JWT（HS256 白名单算法 + jti 登出黑名单）
- 租户隔离：`tenant_id` 仅从 JWT 注入，全链路过滤
- 注入防护：Prompt 注入规则库 + 前端消息渲染 HTML 转义
- 限流：登录 IP 限流 + 租户/用户双维度 + 消息端点限流
- 密钥：`.env`/compose 环境变量注入，禁止入库与硬编码
