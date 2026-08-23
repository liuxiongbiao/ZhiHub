# 密钥轮换、备份恢复与远端仓库 Runbook

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
| 10 | 阿里云短信 | ALIYUN_SMS_ACCESS_KEY_ID / ALIYUN_SMS_ACCESS_KEY_SECRET / ALIYUN_SMS_SIGN_NAME / ALIYUN_SMS_TEMPLATE_CODE | dysms.console.aliyun.com |

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

## 六、备份策略（PRD §4.4）

| 数据 | 周期 | 实现 | 默认保留 |
|---|---|---|---|
| MySQL | 每日全量 | 容器内 `mysqldump --single-transaction`，同时备份 routines/triggers/events | 30 天 |
| Milvus | 每周 | 短暂停写后同步复制 Milvus 本地状态、MinIO 对象和 etcd 元数据并压缩 | 30 天 |
| Elasticsearch | 每周 | Elasticsearch 文件系统仓库 + 原生 Snapshot API | 仓库内快照按运维周期治理 |

备份产物默认写入仓库根目录的 `backups/`，该目录已加入 `.gitignore`。每轮生成独立的 `run-YYYYMMDD-HHMMSS/` 目录和 `manifest.json`，清单包含文件名、SHA-256、恢复验证表数量与 Elasticsearch 快照名。备份目录应再同步到受访问控制的异机或对象存储；只保存在同一块磁盘上不构成灾备。

### 6.1 每日 MySQL 全量与自动恢复验证

```powershell
cd C:\Users\Lenovo\Desktop\Three\zhihub\backend
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 `
  -Scope daily -RetentionDays 30 -VerifyMySqlRestore
```

`-VerifyMySqlRestore` 会创建随机临时库、导入刚生成的 dump、检查业务表数量，然后在 `finally` 中删除临时库。命令只有在 dump 非空且恢复成功时才输出 `BACKUP_SUCCESS`。

正式环境不要把数据库密码写入计划任务文本；应由密钥管理或受限服务账号在启动脚本时注入 `-MySqlPassword`。Windows 任务计划程序建议每天凌晨执行上述命令，并仅对失败退出码告警。

### 6.2 每周 Milvus 与 Elasticsearch 快照

```powershell
cd C:\Users\Lenovo\Desktop\Three\zhihub
docker compose -f deploy\docker-compose.yml up -d --force-recreate elasticsearch
cd backend
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Scope weekly -RetentionDays 30
```

Milvus 快照会短暂停顿知识入库 Worker 与 Milvus 容器，复制完成后无论成功或失败都会自动解除暂停；建议在低峰维护窗口执行。Elasticsearch 的 `path.repo` 和宿主机仓库挂载已经写入 `deploy/docker-compose.yml`，首次使用周备前必须按上面命令重建 Elasticsearch 容器使配置生效。

### 6.3 恢复手册

MySQL 恢复到指定空库：

```powershell
docker exec -e MYSQL_PWD=<密码> deploy-mysql-1 mysql -uroot -e "CREATE DATABASE zhihub_restore CHARACTER SET utf8mb4;"
docker cp C:\path\to\mysql-zhihub-YYYYMMDD-HHMMSS.sql deploy-mysql-1:/tmp/restore.sql
docker exec -e MYSQL_PWD=<密码> deploy-mysql-1 mysql -uroot zhihub_restore -e "source /tmp/restore.sql"
```

Elasticsearch 恢复前先确认目标快照状态为 `SUCCESS`，再在维护窗口调用原生恢复接口；为避免覆盖在线索引，演练时使用重命名恢复：

```powershell
$body = '{"indices":"*","include_global_state":false,"rename_pattern":"(.+)","rename_replacement":"restore_$1"}'
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:9200/_snapshot/zhihub_fs/<快照名>/_restore?wait_for_completion=true' `
  -ContentType 'application/json' -Body $body
```

Milvus 恢复必须在全栈停写的维护窗口进行：先解压 `milvus-physical-*.tar.gz`，停止 backend、knowledge worker、Milvus、MinIO 与 etcd，备份当前卷后，再把归档中的 `milvus/`、`minio_data/`、`etcd/` 分别复制回对应容器卷，最后按 etcd → MinIO → Milvus → Worker → backend 顺序启动并执行检索冒烟。禁止在运行中的卷上覆盖恢复。

### 6.4 RPO 与演练

- 当前 MySQL 日备对应 RPO 约 24 小时；Milvus/ES 周备的索引恢复点最多落后一周，但知识文档源文件和 MySQL 元数据保留后可重建索引。
- 每月至少执行一次 MySQL 自动恢复验证；每季度在隔离环境执行一次 Milvus/ES 恢复演练，并记录耗时、缺失数据与修正动作。
- 恢复完成后必须验证：迁移版本、租户数/文档数、随机三条知识检索、订单双因子查询与后台核心页面。

## 七、阿里云短信真实发送验收

1. 在阿里云控制台准备已启用短信权限的 AccessKey、审核通过的签名与验证码模板；模板参数必须名为 `code`。
2. 只在受控的 `deploy/.env` 或密钥管理系统中填写四项 `ALIYUN_SMS_*` 配置，并设置 `SMS_ENABLED=true`。不要把凭据写入命令行、日志或 Git。
3. 重建 backend 后执行一次真实发送：

```powershell
cd C:\Users\Lenovo\Desktop\Three\zhihub\deploy
docker compose up -d --build backend
docker compose exec backend python scripts/verify_sms.py --phone <验收手机号>
```

脚本只在真实模式运行，不输出验证码或凭据；阿里云返回成功且网关调用耗时低于 3000ms 才会输出 `PASS`。最后还必须由验收手机号人工确认实际收码，因为网关成功回执不等于终端必达。

若凭据未配置，development/test 保留 mock 路径；production 或 `SMS_ENABLED=true` 但配置不全时显式失败，禁止静默伪装发送成功。
