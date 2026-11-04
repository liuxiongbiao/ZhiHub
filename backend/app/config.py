"""
backend/app/config.py

职责: 集中配置类，基于 pydantic-settings 从 .env 读取全部配置项

核心设计:
  - 继承 BaseSettings，字段名小写下划线，自动映射环境变量大写形式
  - 所有字段都有默认值，无 .env 也能启动（开发环境零配置）
  - litellm 通过 os.environ 读取 provider key，故末尾显式同步 DEEPSEEK_API_KEY

依赖关系:
  - 上游: 所有 app.* 模块 import settings
  - 下游: .env 文件
"""

import os
import ipaddress
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # ── 应用 & JWT ──
    app_env: str = "development"                          # 运行环境
    app_secret_key: str = "change-me"                     # JWT 签名密钥（生产必须替换）

    @field_validator("app_secret_key")
    @classmethod
    def _check_secret_key(cls, v: str, info) -> str:
        """非 development 环境禁止使用默认密钥（同时管 JWT 签名 + AES 加密 + HMAC）。"""
        if info.data.get("app_env") != "development" and v == "change-me":
            raise ValueError("生产环境必须配置非默认 app_secret_key（JWT/AES/HMAC 三道防线）")
        return v

    jwt_algorithm: str = "HS256"                          # JWT 签名算法

    @field_validator("jwt_algorithm")
    @classmethod
    def _check_jwt_algorithm(cls, v: str) -> str:
        """算法白名单: 只允许对称/非对称标准算法，禁止 "none" 等可绕过验签的值。"""
        if v not in {"HS256", "RS256"}:
            raise ValueError(f"jwt_algorithm 必须是 HS256/RS256，当前值: {v!r}")
        return v
    jwt_access_token_expire_minutes: int = 120            # access token 有效期（分钟）
    jwt_refresh_token_expire_days: int = 30               # refresh token 有效期（天）

    @field_validator("jwt_refresh_token_expire_days")
    @classmethod
    def _check_refresh_ttl(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("jwt_refresh_token_expire_days 必须大于 0")
        return value

    # ── MySQL（关系型主库）──
    mysql_host: str = "localhost"
    mysql_port: int = 3307
    mysql_user: str = "zhihub"
    mysql_password: str = "zhihub"
    mysql_database: str = "zhihub"

    # ── MongoDB（会话 trace / 文档元数据）──
    mongo_uri: str = "mongodb://localhost:27017/zhihub"

    # ── Redis（JWT 黑名单 / 限流令牌桶 / 在线坐席集合）──
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    # -1: Redis 强制认证（compose 默认 zhihub-dev；生产必须更换）
    redis_password: str = ""

    # 平台运营令牌（企业版审批专用；空值 = 接口 fail-closed）
    platform_ops_token: str = ""

    # 邮箱激活（SMTP 未配置时 development 打日志 / test 返回链接 / production 拒绝启动）
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    email_verify_required: bool = True
    email_verify_token_ttl_hours: int = 24
    email_verify_link_base: str = "http://localhost:5173"
    invite_link_base: str = "http://localhost:5173/accept-invite"
    email_enabled: bool = False
    # development 环境是否允许验证码/激活链接回显（Docker NAT 下无法按来源 IP 区分本机）
    dev_allow_code_reveal: bool = False
    # 演示环境免密角色切换。必须显式开启，且 production 会在启动时强制拒绝，
    # 避免把便捷演示入口误带到真实环境。
    demo_quick_login_enabled: bool = False

    # 外部能力开关：启用时由统一启动校验保证所需凭据完整。
    payment_enabled: bool = False
    sms_enabled: bool = False
    wechat_enabled: bool = False
    llm_enabled: bool = False
    embedding_enabled: bool = False

    # ── RabbitMQ（知识库异步处理队列）──
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # ── Milvus（向量库）──
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # ── Elasticsearch（混合检索 BM25 侧）──
    elasticsearch_url: str = "http://localhost:9200"

    # ── LLM ──
    llm_provider: str = "deepseek"                        # LLM provider
    deepseek_api_key: str = ""                            # DeepSeek API key
    deepseek_base_url: str = "https://api.deepseek.com"   # DeepSeek base URL
    # 规格 005 模型网关: 主/备模型，主模型失败自动切备用（PRD §3.14）。默认 deepseek/deepseek-chat
    llm_primary_model: str = "deepseek/deepseek-chat"     # 主模型
    llm_backup_model: str = "deepseek/deepseek-chat"      # 备用模型（主模型失败时降级；: 建议配不同模型，相同则切换无意义）

    # ── Embedding & Rerank ──
    embedding_provider: str = "siliconflow"               # embedding provider
    embedding_api_key: str = ""                           # embedding API key
    embedding_model: str = "BAAI/bge-m3"                  # 向量模型
    rerank_model: str = "BAAI/bge-reranker-v2-m3"         # 重排序模型

    # 规格 008 可观测性: Langfuse（未配置 key 时 SDK 降级 no-op，不影响主流程）
    langfuse_base_url: str = "http://localhost:3000"      # Langfuse 服务地址（SDK v4 同时认 LANGFUSE_HOST）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # 规格 009 技术栈对比: 工具调用模式（mcp=JSON-RPC 信封 | fc=Function Calling，默认 mcp）
    agent_tool_mode: str = "mcp"

    # CORS 允许来源（逗号分隔，从 .env 读取；默认仅前端开发服务器）
    cors_origins: str = "http://localhost:5173"

    # 对外公共基地址（含 scheme，不带尾斜杠），用于生成 Widget 嵌入代码的
    # 绝对 script/API/WS 地址。为空时：开发环境回退 http://localhost:5173，
    # 生产环境嵌入代码接口显式 409（禁止退回 localhost 制造假闭环）。
    public_base_url: str = ""

    # 规格 010 业务闭环: 退款 / 知识库 / 计费配置
    refund_approval_threshold: float = 1000.0   # 大额退款审批阈值（超过则需审批）
    refund_timeout_days: int = 7                 # 退款超时自动取消天数
    order_payment_timeout_minutes: int = 30      # 订单待支付超时自动取消分钟数（PRD 3.6）
    order_shipped_timeout_days: int = 7          # 订单发货后自动完成天数（PRD 3.6）
    trial_period_days: int = 30                  # 试用版天数，到期降级只读（PRD 3.1）
    tenant_cancellation_retention_days: int = 30 # 注销申请后的数据导出保留窗口（PRD 8.5）
    doc_validity_days: int = 90                  # 知识库文档有效期天数
    quality_garbage_threshold: float = 0.3       # 文档乱码率阈值（超过判为低质）
    storage_doc_limit: int = 50                  # 租户文档数量上限
    invoice_overdue_days: int = 15               # 账单逾期降级天数
    # ── 规格 015 计费准确性（公式与示例见 docs/specs/015-billing-accuracy/spec.md）──
    # 套餐月基础费（元）：trial=0 / basic=999 / pro=4999 / enterprise=0（议价）
    billing_plan_base_fees: str = '{"trial": 0, "basic": 999, "pro": 4999, "enterprise": 0}'
    # 超额单价（元/条）：PRD §3.9 默认 0.5；账单公式 overage_fee = overage_cnt * price
    billing_overage_price: float = 0.5
    # PRD §3.9 多模型按 Token 折算：1 条标准对话对应的 Token 基数（按模型权重再差异化）
    billing_tokens_per_conversation: float = 2500.0
    # 试用租户计费口径（2026-08-12 产品决策 B）：免费但超额按单价计费。
    # true=生成账单，base_fee=0，超额部分按 BILLING_OVERAGE_PRICE 计费；
    # 逾期未付的超额账单按既有规则降级只读。
    billing_charge_trial: bool = True
    # PRD §3.2 知识库存储上限（MB/月）：0=无限制（enterprise 议价）
    storage_quota_mb: str = '{"trial": 500, "basic": 5120, "pro": 51200, "enterprise": 0}'
    # 待决策项：知识库上传文件大小上限（MB）。实现默认 10（PRD 写 50，安全优先）；
    # 产品确认后仅改配置，无需发版。
    upload_max_file_size_mb: int = 10
    upload_storage_dir: str = "./data/uploads"
    ws_pubsub_channel: str = "zhihub:ws:broadcast"
    # 规格 014 Widget 公共通道：Widget 会话令牌有效期（分钟）
    widget_token_expire_minutes: int = 30
    # 规格 007 / PRD §3.4: 单会话最大轮次（原硬编码 50，与 PRD 规定的 15 不符）
    max_rounds_per_session: int = 15
    # 路由置信度。低于该值转人工。这是意图置信度，不是检索精排分。
    router_confidence_threshold: float = 0.4
    # PRD §3.11: DeepAgent 任务执行超时（秒）
    deep_agent_timeout_seconds: int = 600
    # PRD §3.11: 单个 DeepAgent 任务 token 硬预算（输入 + 输出累计）
    deep_agent_token_budget: int = 500_000
    model_weights: str = '{"deepseek/deepseek-chat": 1.0, "deepseek/deepseek-reasoner": 3.0, "gpt-4o": 5.0, "gpt-4o-mini": 0.5, "claude-3-5-sonnet": 4.0}'  # 模型折算权重（JSON 字符串，贵模型权重高）

    # 规格 010 PRD §3.10: 支付宝沙箱支付（key 为空 → mock 模式，不调真实网关；用户后填真 key 即切换）
    alipay_app_id: str = ""                                  # 支付宝应用 ID（沙箱）
    alipay_app_private_key: str = ""                         # 应用私钥（PEM，用于签名）
    alipay_public_key: str = ""                              # 支付宝公钥（PEM，用于验签）
    alipay_gateway: str = "https://openapi.alipaydev.com/gateway.do"  # 沙箱网关
    alipay_sandbox: bool = True                              # 是否沙箱模式
    alipay_notify_url: str = ""                              # 异步回调地址（生产需配置公网可达 URL）

    # PRD §3.1: 微信公众号 OAuth2 网页授权扫码登录（key 为空 → mock 模式，不调真实网关）
    wechat_app_id: str = ""                                  # 微信公众号 AppID
    wechat_app_secret: str = ""                              # 微信公众号 AppSecret
    wechat_redirect_uri: str = ""                            # OAuth 回调地址（生产需配置公网可达 URL）

    # OCR 兜底（PRD §3.3）: 扫描件/图片型 PDF 文本提取失败时调百度 OCR 识别
    # key 为空 → mock 模式，返回固定占位文本（不调真实网关，开发/测试零配置）
    ocr_provider: str = "baidu"                              # OCR 服务商（baidu/none）
    ocr_api_key: str = ""                                    # 百度 OCR API Key
    ocr_secret_key: str = ""                                 # 百度 OCR Secret Key

    # PRD §3.1.2: 阿里云短信（key 为空 → mock 模式，不调真实网关；配置后自动切换真实发送）
    aliyun_sms_access_key_id: str = ""                       # 阿里云 AccessKey ID
    aliyun_sms_access_key_secret: str = ""                   # 阿里云 AccessKey Secret
    aliyun_sms_sign_name: str = ""                           # 短信签名
    aliyun_sms_template_code: str = ""                       # 短信模板 Code（如 SMS_123456）
    aliyun_sms_timeout_seconds: float = 2.8                   # 网关超时（PRD 验收 <3s）

    # PRD §3.2 RagFlow 语义解析：配置后走 RagFlow，未配置/解析失败降级本地规则解析
    ragflow_base_url: str = ""                               # RagFlow 服务地址（如 http://localhost:9380）
    ragflow_api_key: str = ""                                # RagFlow API Key（优先于邮箱密码）
    ragflow_email: str = ""                                  # RagFlow 登录邮箱（无 API Key 时使用）
    ragflow_password: str = ""                               # RagFlow 登录密码
    ragflow_dataset_id: str = ""                             # RagFlow 数据集 ID
    ragflow_parse_timeout_seconds: int = 300                 # 解析轮询超时

    @staticmethod
    def _is_external_https_url(value: str) -> bool:
        """生产回调地址必须是 HTTPS，且不能指向本机或私网 IP。"""
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        hostname = parsed.hostname.lower()
        if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return True
        return not (address.is_private or address.is_loopback or address.is_link_local)

    def _production_credential_issues(self) -> list[str]:
        """生产环境拒绝代码和 compose 里的默认口令，并要求 Mongo/ES 带认证。"""
        issues: list[str] = []
        if self.app_secret_key == "change-me":
            issues.append("APP_SECRET_KEY")
        if self.mysql_password in {"", "zhihub"}:
            issues.append("MYSQL_PASSWORD")
        if self.redis_password in {"", "zhihub-dev"}:
            issues.append("REDIS_PASSWORD")
        if "guest:guest@" in self.rabbitmq_url or "zhihub:zhihub-dev@" in self.rabbitmq_url:
            issues.append("RABBITMQ_URL")
        mongo = urlparse(self.mongo_uri)
        if not mongo.username or not mongo.password:
            issues.append("MONGO_URI (username and password required)")
        elastic = urlparse(self.elasticsearch_url)
        if not elastic.username or not elastic.password:
            issues.append("ELASTICSEARCH_URL (username and password required)")
        return issues

    def _enabled_feature_flags(self) -> dict[str, bool]:
        return {
            "payment": self.payment_enabled,
            "sms": self.sms_enabled,
            "wechat": self.wechat_enabled,
            "email": self.email_verify_required and (
                self.email_enabled or self.app_env == "production"
            ),
            "llm": self.llm_enabled,
            "embedding": self.embedding_enabled,
        }

    def _missing_enabled_settings(self, required: dict, enabled: dict) -> list[str]:
        missing = [
            field.upper()
            for feature, fields in required.items()
            if enabled[feature]
            for field in fields
            if not str(getattr(self, field, "")).strip()
        ]
        if (
            self.app_env == "production"
            and enabled["email"]
            and not self._is_external_https_url(self.email_verify_link_base)
        ):
            missing.append("EMAIL_VERIFY_LINK_BASE (external HTTPS URL)")
        if self.app_env == "production" and self.demo_quick_login_enabled:
            missing.append("DEMO_QUICK_LOGIN_ENABLED (must be false in production)")
        if self.app_env == "production":
            missing.extend(self._production_credential_issues())
        return missing

    @model_validator(mode="after")
    def _validate_enabled_features(self) -> "Settings":
        """按功能开关一次性报告全部缺失配置，避免首次调用才失败。"""
        required: dict[str, tuple[str, ...]] = {
            "payment": (
                "alipay_app_id", "alipay_app_private_key", "alipay_public_key", "alipay_notify_url",
            ),
            "sms": (
                "aliyun_sms_access_key_id", "aliyun_sms_access_key_secret",
                "aliyun_sms_sign_name", "aliyun_sms_template_code",
            ),
            "wechat": ("wechat_app_id", "wechat_app_secret", "wechat_redirect_uri"),
            "email": ("smtp_host", "smtp_user", "smtp_password", "smtp_from"),
            "llm": ("deepseek_api_key",),
            "embedding": ("embedding_api_key",),
        }
        enabled = self._enabled_feature_flags()
        missing = self._missing_enabled_settings(required, enabled)
        if missing:
            raise ValueError("enabled features have missing configuration: " + ", ".join(missing))
        return self

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()

# litellm 通过 os.environ 读取 provider API key（而非 pydantic settings 对象）
# 此处显式同步，确保 litellm 能取到 DeepSeek key
os.environ.setdefault("DEEPSEEK_API_KEY", settings.deepseek_api_key)
