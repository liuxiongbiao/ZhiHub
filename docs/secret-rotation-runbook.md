# 密钥轮换与远端仓库 Runbook（P0-3 / P1-10）

> 状态：2026-08-12 审计确认 backend/.env 中存在 9 组真实凭据明文。
> 实际轮换必须由持有各控制台权限的人执行；本文件只提供步骤。

## 一、需轮换的密钥

| # | 服务 | .env 变量 | 控制台入口 |
|---|---|---|---|
| 1 | DeepSeek | DEEPSEEK_API_KEY | platform.deepseek.com - API Keys |
| 2 | SiliconFlow | EMBEDDING_API_KEY | cloud.siliconflow.cn - API 密钥 |
| 3 | 支付宝沙箱 | ALIPAY_APP_ID / ALIPAY_APP_PRIVATE_KEY / ALIPAY_PUBLIC_KEY | open.alipay.com 沙箱应用 |
| 4 | 微信公众号 | WECHAT_APP_ID / WECHAT_APP_SECRET | mp.weixin.qq.com - 基本配置 |
| 5 | 百度 OCR | OCR_API_KEY / OCR_SECRET_KEY | console.bce.baidu.com - OCR |
| 6 | Langfuse | LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY | cloud.langfuse.com - API Keys |
| 7 | MySQL | MYSQL_PASSWORD / MYSQL_ROOT_PASSWORD | docker exec mysql - ALTER USER |
| 8 | Redis | REDIS_PASSWORD | redis-cli CONFIG SET requirepass |
| 9 | RabbitMQ | RABBITMQ_PASS | rabbitmq 管理台重建用户 |

## 二、通用轮换步骤

1. 在控制台生成新凭据，保留旧凭据直到验证通过。
2. 更新 backend/.env 与 deploy/.env（生产用密钥管理/CI Secrets）。
3. 重启：docker compose up -d --force-recreate backend。
4. 验证：/health 200、登录/支付/检索冒烟通过。
5. 删除旧凭据并记录作废时间到第五节。

## 三、本地 .env 权限收紧（Windows）

```powershell
icacls "C:\Users\Lenovo\Desktop\Three\zhihub\backend\.env" /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

## 四、远端仓库（P1-10）

```bash
cd C:/Users/Lenovo/Desktop/Three/zhihub
git remote add origin <私有仓库地址>
git push -u origin master
# 推送前确认无敏感文件：
git ls-files | Select-String -Pattern '\.env$|\.log$|cred'
```

推送完成后：CI 首次真实运行；audit-archive 中的本地 bundle 降级为历史归档。

## 五、轮换变更记录

| 日期 | 服务 | 旧值作废时间 | 执行人 |
|---|---|---|---|
| 待填 | | | |
