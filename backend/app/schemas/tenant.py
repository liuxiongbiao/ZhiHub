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
    status: str
    current_user: CurrentUser
    # 008 降级模式告警横幅（PRD 3.4/3.8）：检索基础设施故障时 True
    degradation_alert: bool = False
    # 009 预警站内红色横幅（PRD 3.9）：配额 80%/100% 预警，None=无预警
    warning_banner: str | None = None


# ---------- 配置向导（PRD 3.1）----------
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
    expires_in: int


class AcceptInviteRequest(BaseModel):
    """接受邀请请求：token + 手机号 + 密码。"""
    token: str
    phone: str
    password: str


class AcceptInviteResponse(BaseModel):
    """接受邀请成功响应：创建的成员关联信息。"""
    user_id: int
    tenant_id: int
    role: str


# ---------- Widget 品牌定制（PRD 3.8 / 020）----------
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


class WidgetEmbedCodeResponse(BaseModel):
    """Widget 嵌入代码响应：可粘贴到客户网站的 script 片段。"""
    tenant_id: int
    embed_code: str
