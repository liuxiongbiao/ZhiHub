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

from pydantic import field_validator
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
        """算法白名单（T33）: 只允许对称/非对称标准算法，禁止 "none" 等可绕过验签的值。"""
        if v not in {"HS256", "RS256"}:
            raise ValueError(f"jwt_algorithm 必须是 HS256/RS256，当前值: {v!r}")
        return v
    jwt_access_token_expire_minutes: int = 120            # access token 有效期（分钟）

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
    # 005 模型网关: 主/备模型，主模型失败自动切备用（PRD 3.14）。默认 deepseek/deepseek-chat
    llm_primary_model: str = "deepseek/deepseek-chat"     # 主模型
    llm_backup_model: str = "deepseek/deepseek-chat"      # 备用模型（主模型失败时降级；T24: 建议配不同模型，相同则切换无意义）

    # ── Embedding & Rerank ──
    embedding_provider: str = "siliconflow"               # embedding provider
    embedding_api_key: str = ""                           # embedding API key
    embedding_model: str = "BAAI/bge-m3"                  # 向量模型
    rerank_model: str = "BAAI/bge-reranker-v2-m3"         # 重排序模型

    # 008 可观测性: Langfuse（未配置 key 时 SDK 降级 no-op，不影响主流程）
    langfuse_base_url: str = "http://localhost:3000"      # Langfuse 服务地址（SDK v4 同时认 LANGFUSE_HOST）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # 009 技术栈对比: 工具调用模式（mcp=JSON-RPC 信封 | fc=Function Calling，默认 mcp）
    agent_tool_mode: str = "mcp"

    # P-011: CORS 允许来源（逗号分隔，从 .env 读取；默认仅前端开发服务器）
    cors_origins: str = "http://localhost:5173"

    # 010 业务闭环: 退款 / 知识库 / 计费配置
    refund_approval_threshold: float = 1000.0   # 大额退款审批阈值（超过则需审批）
    refund_timeout_days: int = 7                 # 退款超时自动取消天数
    order_payment_timeout_minutes: int = 30      # 订单待支付超时自动取消分钟数（PRD 3.6）
    order_shipped_timeout_days: int = 7          # 订单发货后自动完成天数（PRD 3.6）
    trial_period_days: int = 30                  # 试用版天数，到期降级只读（PRD 3.1）
    doc_validity_days: int = 90                  # 知识库文档有效期天数
    quality_garbage_threshold: float = 0.3       # 文档乱码率阈值（超过判为低质）
    storage_doc_limit: int = 50                  # 租户文档数量上限
    invoice_overdue_days: int = 15               # 账单逾期降级天数
    # ── 015 计费准确性（公式与示例见 specs/015-billing-accuracy/spec.md）──
    # 套餐月基础费（元）：trial=0 / basic=999 / pro=4999 / enterprise=0（议价）
    billing_plan_base_fees: str = '{"trial": 0, "basic": 999, "pro": 4999, "enterprise": 0}'
    # 超额单价（元/条）：PRD 3.9 默认 0.5；账单公式 overage_fee = overage_cnt * price
    billing_overage_price: float = 0.5
    # 试用租户是否生成账单：默认 false（业务决策项，见修复清单待决策区；产品可改配置切换）
    billing_charge_trial: bool = False
    # PRD 3.2 知识库存储上限（MB/月）：0=无限制（enterprise 议价）
    storage_quota_mb: str = '{"trial": 500, "basic": 5120, "pro": 51200, "enterprise": 0}'
    # 014 Widget 公共通道：Widget 会话令牌有效期（分钟）
    widget_token_expire_minutes: int = 30
    # 007/PRD 3.4: 单会话最大轮次（原硬编码 50 与 PRD 15 不符）
    max_rounds_per_session: int = 15
    model_weights: str = '{"deepseek/deepseek-chat": 1.0, "deepseek/deepseek-reasoner": 3.0, "gpt-4o": 5.0, "gpt-4o-mini": 0.5, "claude-3-5-sonnet": 4.0}'  # 模型折算权重（JSON 字符串，贵模型权重高）

    # 010 PRD 3.10: 支付宝沙箱支付（key 为空 -> mock 模式，不调真实网关；用户后填真 key 即切换）
    alipay_app_id: str = ""                                  # 支付宝应用 ID（沙箱）
    alipay_app_private_key: str = ""                         # 应用私钥（PEM，用于签名）
    alipay_public_key: str = ""                              # 支付宝公钥（PEM，用于验签）
    alipay_gateway: str = "https://openapi.alipaydev.com/gateway.do"  # 沙箱网关
    alipay_sandbox: bool = True                              # 是否沙箱模式
    alipay_notify_url: str = ""                              # 异步回调地址（生产需配置公网可达 URL）

    # PRD 3.1: 微信公众号 OAuth2 网页授权扫码登录（key 为空 -> mock 模式，不调真实网关）
    wechat_app_id: str = ""                                  # 微信公众号 AppID
    wechat_app_secret: str = ""                              # 微信公众号 AppSecret
    wechat_redirect_uri: str = ""                            # OAuth 回调地址（生产需配置公网可达 URL）

    # 019 OCR 兜底（PRD 3.3）: 扫描件/图片型 PDF 文本提取失败时调百度 OCR 识别
    # key 为空 -> mock 模式，返回固定占位文本（不调真实网关，开发/测试零配置）
    ocr_provider: str = "baidu"                              # OCR 服务商（baidu/none）
    ocr_api_key: str = ""                                    # 百度 OCR API Key
    ocr_secret_key: str = ""                                 # 百度 OCR Secret Key

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()

# litellm 通过 os.environ 读取 provider API key（而非 pydantic settings 对象）
# 此处显式同步，确保 litellm 能取到 DeepSeek key
os.environ.setdefault("DEEPSEEK_API_KEY", settings.deepseek_api_key)
