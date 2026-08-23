# ZhiHub 真实凭据申请与配置指南

> 更新日期：2026-08-16。本文只说明需要哪些凭据以及如何申请，不保存任何真实密钥。真实值只能写入部署环境的 `deploy/.env` 或密钥管理系统，`.env` 永远不得提交 Git。

## 使用原则

- 本地演示默认关闭 `PAYMENT_ENABLED`、`SMS_ENABLED`、`WECHAT_ENABLED`、`EMAIL_ENABLED`、`LLM_ENABLED`、`EMBEDDING_ENABLED`，支付、短信、微信和模型调用可使用项目的 development/mock 路径。
- production 环境启用任一功能开关后，启动校验会要求对应配置完整；不要用示例值、沙箱私钥或个人邮箱授权码冒充生产凭据。
- `APP_SECRET_KEY`、数据库密码、Redis/RabbitMQ 密码等内部密钥应由密码管理器或部署平台生成和托管，不应向第三方平台申请。
- 审核时长来自各平台当前公开说明，只能作为估计；节假日、资质补件和行业类型都可能延长审核。

## 外部服务凭据

| 服务 | 涉及的 `.env` key | 去哪里申请（入口与步骤） | 资质与大致时长 | 演示是否必需 | 填完后解锁 |
|---|---|---|---|---|---|
| 支付宝沙箱 | `PAYMENT_ENABLED=true`、`ALIPAY_APP_ID`、`ALIPAY_APP_PRIVATE_KEY`、`ALIPAY_PUBLIC_KEY`、`ALIPAY_GATEWAY`、`ALIPAY_SANDBOX=true`、`ALIPAY_NOTIFY_URL` | 进入[支付宝开放平台沙箱](https://open.alipay.com/develop/sandbox/app)，登录后进入开发者中心 → 沙箱应用；生成 RSA2 应用密钥，上传应用公钥，然后复制沙箱 AppID 与支付宝公钥。回调地址需是支付宝可访问的 HTTPS URL。 | 沙箱通常登录后即可使用，个人账号可做联调；正式支付通常需要企业/个体工商户主体、应用审核和产品签约，常见为数小时至数个工作日。 | 否。关闭支付开关可演示内部订单与 mock 支付状态。 | 真实沙箱下单、异步支付通知、渠道退款查询与退款幂等链路。 |
| 阿里云短信 | `SMS_ENABLED=true`、`ALIYUN_SMS_ACCESS_KEY_ID`、`ALIYUN_SMS_ACCESS_KEY_SECRET`、`ALIYUN_SMS_SIGN_NAME`、`ALIYUN_SMS_TEMPLATE_CODE` | 进入[阿里云短信服务](https://help.aliyun.com/zh/sms/)，完成实名认证并开通服务；在控制台提交短信资质、签名和验证码模板；创建仅含短信权限的 RAM 用户并生成 AccessKey。不要使用主账号 AccessKey。 | 国内短信需要实名资质；企业资质最稳妥，个人账号使用企业签名通常还需授权材料。资质审核官方提示约 2 个工作日，签名/模板通常约 2 小时，特殊主体可能更久。 | 否。development 下 mock 会生成验证码；真实手机号收码才需要。 | 手机验证码注册、短信登录、找回密码与绑定手机号。 |
| SMTP 邮箱 | `EMAIL_ENABLED=true`、`SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`SMTP_FROM`、`EMAIL_VERIFY_REQUIRED=true`、`EMAIL_VERIFY_LINK_BASE` | 选择支持 SMTP 的邮箱或企业邮服务，在邮箱安全设置中启用 SMTP/STARTTLS，并生成“客户端授权码/应用密码”；也可使用阿里云邮件推送、腾讯企业邮等事务邮件服务。`SMTP_PASSWORD` 应填写授权码而不是网页登录密码。 | 个人邮箱一般即时开通；企业域名邮箱通常要求验证域名所有权和配置 SPF/DKIM，约数分钟至 1 个工作日。批量外发可能另需发信域名审核。 | 否。development 可关闭邮件验证；要演示真实激活邮件必须配置。 | 邮箱注册激活、重发激活邮件和邮件验证码。 |
| 微信网站扫码登录 | `WECHAT_ENABLED=true`、`WECHAT_APP_ID`、`WECHAT_APP_SECRET`、`WECHAT_REDIRECT_URI` | 登录[微信开放平台](https://open.weixin.qq.com/)，完成开发者资质认证，在管理中心创建“网站应用”，填写官网、应用资料和授权回调域，审核通过后取得 AppID/AppSecret；回调 URI 必须与后台配置一致。 | 网站应用通常要求已认证的企业、组织或合规主体以及可访问网站；开放平台认证和应用审核通常为 1–7 个工作日，补件会延长。个人主体可用能力受限。 | 否。development 下可走 mock openid；真实扫码必须申请。 | 微信扫码登录、openid 绑定与回调校验。 |
| DeepSeek LLM | `LLM_ENABLED=true`、`LLM_PROVIDER=deepseek`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`，以及可选 `LLM_PRIMARY_MODEL`、`LLM_BACKUP_MODEL` | 登录[DeepSeek 开放平台](https://platform.deepseek.com/)，完成账号验证和充值，在 API Keys 页面新建密钥；调用方式见[官方 API 文档](https://api-docs.deepseek.com/)。 | 通常个人账号即可申请，API Key 即时生成；可用额度取决于充值、风控和平台当前政策，一般无人工应用审核。 | 否。不开启时可演示人工客服、工单、订单等；真实智能问答需要。 | 多智能体意图判断、知识回答生成、Deep Agent 报告与模型计费。 |
| SiliconFlow Embedding / Rerank | `EMBEDDING_ENABLED=true`、`EMBEDDING_PROVIDER=siliconflow`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL=BAAI/bge-m3`、`RERANK_MODEL=BAAI/bge-reranker-v2-m3` | 登录[SiliconFlow](https://cloud.siliconflow.cn/)，进入“API 密钥”页面创建密钥；参考[官方快速上手](https://docs.siliconflow.cn/cn/userguide/quickstart)，确认目标模型当前可用并充值额度。 | 手机或邮箱账号通常可即时创建 key；高额度或特定模型可能要求实名/充值，无常规人工审核。 | 仅知识库语义检索演示需要；普通管理页面不需要。 | 文档向量化、Milvus 语义召回和 Rerank 精排。 |
| Langfuse Cloud | `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL` | 注册[Langfuse Cloud](https://cloud.langfuse.com/)，创建 organization/project，在 Project Settings → API Keys 创建并复制 public/secret key；接口说明见[Langfuse 官方文档](https://langfuse.com/docs/api-and-data-platform/features/public-api)。 | 邮箱账号通常即时开通，无企业审核；企业合规、区域或 HIPAA 套餐需另行签约。 | 否。缺失时 trace 上报降级，不影响核心业务。 | LLM trace、prompt/调用链观测、评分与成本分析。 |
| Langfuse 自托管 | `LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`；部署侧还需 `LANGFUSE_DB_USER`、`LANGFUSE_DB_PASSWORD`、`CLICKHOUSE_USER`、`CLICKHOUSE_PASSWORD`、`LANGFUSE_NEXTAUTH_SECRET`、`LANGFUSE_SALT`、`LANGFUSE_NEXTAUTH_URL` | 使用仓库 compose 的 `observability` profile 启动后，在本地 Langfuse 首次注册并创建项目，再从项目设置复制 key。`NEXTAUTH_SECRET` 和 `SALT` 用密码学安全随机数生成。 | 无外部审核；需要自行负责服务器、备份、TLS 和访问控制。 | 否。 | 与 Cloud 相同的 trace 能力，数据保留在自管环境。 |
| Milvus 本地 | `MILVUS_HOST=milvus`、`MILVUS_PORT=19530` | 仓库 compose 已提供 Milvus、etcd、MinIO，不需要外部凭据。部署方式见[Milvus 官方文档](https://milvus.io/docs)。 | 无资质与审核。 | 混合检索演示需要，但本地 compose 即可满足。 | 向量存储、语义检索和长期记忆。 |
| 托管 Milvus / Zilliz Cloud | 当前代码只有 `MILVUS_HOST`、`MILVUS_PORT`，尚无 token 配置项 | 在[Zilliz Cloud](https://cloud.zilliz.com/)创建集群，取得 Public Endpoint 与 API key。正式接入前需要扩展项目配置，例如新增 `MILVUS_URI`、`MILVUS_TOKEN`，不能把 token 拼进日志或 URL。 | 邮箱注册和试用通常即时；付费生产集群按套餐开通，企业合同另计。 | 否；本地 Milvus 足够演示。 | 托管向量库、高可用与弹性扩容。当前代码需先补认证适配。 |
| Elasticsearch 本地 | `ELASTICSEARCH_URL=http://elasticsearch:9200` | compose 已提供关闭安全认证的单节点 ES，仅限本地开发。生产环境不得沿用 `xpack.security.enabled=false`。 | 无资质与审核。 | 混合检索/BM25 演示需要，但不需要真实凭据。 | BM25 全文召回与混合检索。 |
| Elastic Cloud | 当前代码只有 `ELASTICSEARCH_URL`，尚无用户名、密码或 API key 配置项 | 在[Elastic Cloud](https://cloud.elastic.co/)注册并创建 Hosted deployment 或 Serverless 项目；在项目安全设置创建 Elasticsearch API key。官方创建流程见[Elastic Cloud 文档](https://www.elastic.co/docs/deploy-manage/deploy/elastic-cloud/create-an-organization)。接入前需要给项目增加认证 header 配置。 | 邮箱账号可开启试用，通常即时；付费和企业合同另计。 | 否；本地 ES 足够演示。 | 托管全文检索、备份与高可用。当前代码需先补认证适配。 |
| 百度 OCR | `OCR_PROVIDER=baidu`、`OCR_API_KEY`、`OCR_SECRET_KEY` | 登录[百度智能云 OCR 控制台](https://console.bce.baidu.com/ai/#/ai/ocr/overview/index)，创建 OCR 应用，选择需要的文字识别能力，复制 API Key 与 Secret Key。 | 百度账号实名认证；普通接口通常创建应用后即可调用，企业增值能力和更高额度可能需企业认证，约即时至数个工作日。 | 否。没有真实 key 时不要演示扫描件 OCR。 | 扫描 PDF/图片文字识别与知识库入库。 |
| RAGFlow（可选解析后端） | `RAGFLOW_BASE_URL`、`RAGFLOW_API_KEY`；无 API key 时为 `RAGFLOW_EMAIL`、`RAGFLOW_PASSWORD`；另需 `RAGFLOW_DATASET_ID` | 自托管或使用可访问的 RAGFlow 实例，在用户设置/API 页面创建 API key，再建立 dataset 并复制 dataset id。参考[RAGFlow 官方仓库](https://github.com/infiniflow/ragflow)和[文档](https://ragflow.io/docs/)。 | 自托管无审核，但资源需求较高；托管服务按供应商政策。优先使用 API key，不建议保存登录密码。 | 否。项目内置解析路径可完成常规演示。 | 复杂 PDF 解析、外部数据集同步和解析状态轮询。 |

## 内部生成而非申请的密钥

| Key | 如何生成/管理 | 用途与注意事项 |
|---|---|---|
| `APP_SECRET_KEY` | 使用密码管理器或 `openssl rand -hex 32` 生成；各 API、worker 必须完全一致 | JWT 签名和 PII 加密派生。变更会导致旧 token 失效，并可能使既有加密数据无法解密，必须按密钥轮换方案操作。 |
| `PLATFORM_OPS_TOKEN` | 生成至少 32 字节随机值，单独授予运营调用方 | 平台级运维接口认证，不能与管理员密码复用。 |
| `MYSQL_ROOT_PASSWORD` | 仅初始化数据库使用，随机生成并限制保存范围 | 不供应用连接；应用使用最小权限的 `MYSQL_USER`/`MYSQL_PASSWORD`。 |
| `MYSQL_PASSWORD` | 为应用数据库账号生成独立随机密码 | API、Celery、migration 三者必须一致。 |
| `REDIS_PASSWORD` | 随机生成，生产 Redis 同时限制网络访问 | presence、限流、缓存、幂等和 WS Pub/Sub。 |
| `RABBITMQ_USER`、`RABBITMQ_PASS` | 创建独立 vhost/用户并授予所需队列权限 | Celery broker；不要使用 guest 或示例密码。 |
| `MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY` | 自托管 MinIO 中创建专用 service account | Milvus 对象存储。当前 compose 示例值仅限本地。 |
| `LANGFUSE_NEXTAUTH_SECRET`、`LANGFUSE_SALT` | 各自生成独立随机值 | 自托管 Langfuse 会话和加密；示例值禁止用于公网部署。 |

## 最小演示配置建议

完全本地演示无需任何第三方真实凭据：

```dotenv
APP_ENV=development
PAYMENT_ENABLED=false
SMS_ENABLED=false
WECHAT_ENABLED=false
EMAIL_ENABLED=false
LLM_ENABLED=false
EMBEDDING_ENABLED=false
EMAIL_VERIFY_REQUIRED=false
```

若要演示“知识库真实智能回答”，最少再申请并填写：

```dotenv
LLM_ENABLED=true
DEEPSEEK_API_KEY=<从 DeepSeek 平台取得>
EMBEDDING_ENABLED=true
EMBEDDING_API_KEY=<从 SiliconFlow 平台取得>
```

若要演示完整注册触达链路，再分别开启 SMTP、阿里云短信或微信；不要为了演示而同时开启尚未配齐凭据的功能开关。

## 上线前检查

1. 使用 `git status --ignored` 确认 `.env` 被忽略，并用密钥扫描工具检查提交历史。
2. 所有生产回调地址使用公网 HTTPS，支付宝通知、微信 OAuth、邮件激活域名分别做真实回调验证。
3. 第三方 key 使用最小权限、独立项目和额度告警；开发、测试、生产环境完全隔离。
4. 记录密钥所有者、创建日期、到期日和轮换流程；人员离职或疑似泄露时立即撤销。
5. 外部 Milvus/Elastic 接入前先补齐认证配置，不能直接把当前无认证的本地连接方式暴露到公网。
