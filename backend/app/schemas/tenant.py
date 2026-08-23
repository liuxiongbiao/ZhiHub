"""
backend/app/schemas/tenant.py

职责: 租户信息、后台首页响应 Pydantic 模型
"""

from pydantic import BaseModel, Field


class CurrentUser(BaseModel):
    """当前登录用户概要（用户 ID + 租户内角色）。"""
    user_id: int
    role: str


class TenantHomeResponse(BaseModel):
    """后台首页响应：租户信息 + 当前用户概要 + 告警横幅。"""
    tenant_id: int
    tenant_name: str
    plan_type: str
    pending_plan_type: str | None = None
    status: str
    current_user: CurrentUser
    # 规格 008 降级模式告警横幅（PRD §3.4/3.8）：检索基础设施故障时 True
    degradation_alert: bool = False
    # 规格 009 预警站内红色横幅（PRD §3.9）：配额 80%/100% 预警，None=无预警
    warning_banner: str | None = None


# ────────── 配置向导（PRD §3.1）──────────
class WizardStatusResponse(BaseModel):
    """向导状态响应：当前步骤 + 完成度 + 步骤全表。"""
    current_step: str
    completed: bool
    total_steps: int
    steps: list[str]


class InviteRequest(BaseModel):
    """邀请坐席请求：被邀请者手机号。"""
    phone: str


class InviteResponse(BaseModel):
    """邀请成功响应：一次性 token + 有效期（秒）。"""
    invite_token: str
    invite_url: str
    expires_in: int


class AcceptInviteRequest(BaseModel):
    """接受邀请请求：token + 手机号 + 密码。"""
    token: str
    phone: str | None = None
    password: str | None = None


class AcceptInviteResponse(BaseModel):
    """接受邀请成功响应：创建的成员关联信息。"""
    user_id: int
    tenant_id: int
    role: str
    access_token: str
    refresh_token: str


# ────────── Widget 品牌定制（PRD §3.8 / ）──────────
class WidgetConfigRequest(BaseModel):
    """Widget 品牌配置请求：主题色/Logo/欢迎语（均可选，传哪个改哪个）。"""
    theme_color: str | None = Field(default=None, max_length=20)
    logo_url: str | None = Field(default=None, max_length=500)
    welcome_text: str | None = Field(default=None, max_length=500)


class WidgetConfigResponse(BaseModel):
    """Widget 品牌配置响应：当前完整配置。"""
    tenant_id: int
    theme_color: str
    logo_url: str | None = None
    welcome_text: str | None = None


class WidgetConfigQueryResponse(WidgetConfigResponse):
    """: Widget 配置查询响应--品牌字段 + 域名白名单（不含 API Key 任何形态）。"""
    domains: list[str] = Field(default_factory=list)


class WidgetDomainsResponse(BaseModel):
    """: Widget 域名白名单查询响应。"""
    domains: list[str] = Field(default_factory=list)


class WidgetEmbedCodeResponse(BaseModel):
    """Widget 嵌入代码响应：可粘贴到客户网站的 script 片段。"""
    tenant_id: int
    embed_code: str


# ────────── 退款审批阈值（PRD §3.6 规则配置）──────────
class RefundThresholdRequest(BaseModel):
    """退款审批阈值请求：大额退款（>阈值）需管理员二次审批。"""
    refund_approval_threshold: float = Field(..., gt=0, le=1000000, description="阈值金额（元），大于0且不超过6位整数+2位小数")


class RefundThresholdResponse(BaseModel):
    """退款审批阈值响应。"""
    tenant_id: int
    refund_approval_threshold: float


# ────────── 套餐升级（PRD §3.9）──────────
class PlanOptionResponse(BaseModel):
    """套餐卡片响应：价格、配额、存储和席位上限。"""

    plan_type: str
    name: str
    monthly_price: float | None = None
    conversation_quota: int | None = None
    knowledge_base_limit: int | None = None
    storage_quota_mb: int | None = None
    seat_limit: int | None = None
    negotiable: bool = False


class PlanCatalogResponse(BaseModel):
    """套餐目录及租户当前、待议价套餐。"""

    current_plan: str
    pending_plan: str | None = None
    plans: list[PlanOptionResponse]


class PlanChangeRequest(BaseModel):
    """套餐升级请求；试用版和降级请求由业务层拒绝。"""

    target_plan: str = Field(..., min_length=2, max_length=20)


class PlanChangeResponse(BaseModel):
    """套餐升级结果；企业版返回 pending_negotiation。"""

    tenant_id: int
    current_plan: str
    pending_plan: str | None = None
    change_status: str
    seat_limit: int
    conversation_quota: int
