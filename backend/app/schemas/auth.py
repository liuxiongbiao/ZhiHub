"""
backend/app/schemas/auth.py

职责: 注册/登录/登出/验证码/邮箱/密码找回/绑定 请求体与响应 Pydantic 模型（Pydantic v2）
"""

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    """注册请求体：手机号 + 密码 + 企业名称。"""
    phone: str = Field(..., description="手机号（登录名）")
    password: str = Field(..., description="登录密码，需满足强度要求")
    tenant_name: str = Field(..., min_length=1, description="企业名称")
    code: str = Field(..., min_length=4, max_length=8, description="sms code (PRD 3.1.1 US1)")


class RegisterResponse(BaseModel):
    """注册响应：用户 ID、租户 ID、手机号、角色、套餐类型。"""
    user_id: int
    tenant_id: int
    phone: str
    role: str
    plan_type: str


class LoginRequest(BaseModel):
    """登录请求体：手机号 + 密码。"""
    phone: str
    password: str


class LoginResponse(BaseModel):
    """登录响应：短效 access token、可轮换 refresh token 及身份信息。"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: int
    tenant_id: int
    role: str
    phone: str | None = None


class DemoLoginRequest(BaseModel):
    """演示环境快速登录：只接受固定角色代号，不接收账号或密码。"""

    model_config = ConfigDict(extra="forbid")
    persona: str = Field(..., pattern="^(admin|agent1|agent2)$")


class PublicCapabilitiesResponse(BaseModel):
    """公开能力状态；只暴露布尔开关，不返回凭据、地址或内部环境名。"""

    password_login: bool = True
    email_login: bool = False
    sms_login: bool = False
    wechat_login: bool = False
    payment: bool = False
    demo_quick_login: bool = False
    demo_personas: list[str] = Field(default_factory=list)


class MessageResponse(BaseModel):
    """通用消息响应（登出等仅返回提示文案的接口）。"""
    message: str


class RefreshRequest(BaseModel):
    """Refresh token 轮换请求。"""
    refresh_token: str = Field(..., min_length=32, max_length=256)


class RefreshResponse(BaseModel):
    """轮换后的新 token 对。"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    """登出时可携带 refresh token，以撤销整个登录家族。"""
    refresh_token: str | None = Field(default=None, min_length=32, max_length=256)


class RevokeUserTokensRequest(BaseModel):
    """管理员踢出指定用户。"""
    user_id: int = Field(..., gt=0)


class WsTokenResponse(BaseModel):
    """坐席工作台 WebSocket 短效令牌"""
    token: str
    expires_in: int


# ────────── 账户体系扩展（PRD §3.1 #10-#17）──────────

class SendCodeRequest(BaseModel):
    """发送短信验证码请求体（#10）。"""
    phone: str = Field(..., description="手机号")


class SendGenericCodeRequest(BaseModel):
    """通用验证码发送请求体（个人中心/绑定场景，规格 013）。"""
    target: str = Field(..., description="手机号（code_type=sms）或邮箱（code_type=email）")
    code_type: str = Field("sms", description="验证码类型：sms 或 email")


class SendCodeResponse(BaseModel):
    """发送验证码响应（demo 返回 code 供测试，生产环境移除）。"""
    message: str = "验证码已发送"
    code: str = Field(..., description="验证码（demo 模式返回，生产环境不返回）")
    expires_in: int = Field(..., description="验证码有效期（秒）")


class SmsLoginRequest(BaseModel):
    """验证码登录请求体（#10 login_by_code）。"""
    phone: str
    code: str = Field(..., min_length=4, max_length=8, description="短信验证码")


class EmailRegisterRequest(BaseModel):
    """邮箱注册请求体（#11）。"""
    email: str = Field(..., description="邮箱（登录名）")
    password: str = Field(..., description="登录密码，需满足强度要求")
    tenant_name: str = Field(..., min_length=1, description="企业名称")
    plan_type: str = Field("trial", description="套餐类型：trial/basic/pro/enterprise")


class EmailRegisterResponse(BaseModel):
    """邮箱注册响应。"""
    user_id: int
    tenant_id: int
    email: str
    role: str
    plan_type: str
    email_verified: bool = False
    activation_link: str | None = None


class EmailActivateRequest(BaseModel):
    """邮箱激活请求"""
    token: str = Field(..., min_length=16, max_length=128)


class EmailActivateResponse(BaseModel):
    """邮箱激活结果"""
    user_id: int
    email: str
    activated: bool = True


class EmailResendRequest(BaseModel):
    """重新发送激活邮件请求"""
    email: str = Field(..., description="注册邮箱")


class EmailResendResponse(BaseModel):
    """重新发送激活邮件结果"""
    sent: bool


class EmailLoginRequest(BaseModel):
    """邮箱登录请求体（#11）。"""
    email: str
    password: str


class ResetPasswordRequest(BaseModel):
    """密码找回请求体（#16）。"""
    target: str = Field(..., description="手机号（code_type=sms）或邮箱（code_type=email）")
    code: str = Field(..., description="验证码")
    new_password: str = Field(..., description="新密码")
    code_type: str = Field("sms", description="验证码类型：sms 或 email")


class BindEmailRequest(BaseModel):
    """绑定邮箱请求体（#15）。"""
    email: str = Field(..., description="待绑定邮箱")
    code: str = Field(..., description="邮箱验证码")


class BindPhoneRequest(BaseModel):
    """绑定手机号请求体（PRD 3.1 账号绑定反向闭环）。"""
    phone: str = Field(..., description="待绑定手机号")
    code: str = Field(..., description="短信验证码")


class EnterpriseApproveRequest(BaseModel):
    """企业版审批请求体（#17 admin 审批）。"""
    tenant_id: int = Field(..., description="待审批租户 ID")


class EnterpriseApproveResponse(BaseModel):
    """企业版审批响应。"""
    tenant_id: int
    status: str


# ── 微信扫码登录（PRD §3.1）──
class WechatAuthResponse(BaseModel):
    """微信授权 URL 响应：前端跳转到此 URL 触发扫码。"""
    auth_url: str


class WechatCallbackResponse(BaseModel):
    """微信回调响应：已绑定返回 token，未绑定返回 need_bind + openid。"""
    need_bind: bool
    openid: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    user_id: int | None = None
    tenant_id: int | None = None
    role: str | None = None


class WechatBindRequest(BaseModel):
    """微信绑定请求体：已登录用户传入微信授权 code，服务端换 openid 后绑定。

    不收 openid：openid 必须由微信授权（code 交换）产生，防"客户端任意传 openid
    绑定他人微信"的账户接管链。
    """
    code: str = Field(..., min_length=1, description="微信授权 code（一次性）")


class WechatBindResponse(BaseModel):
    """微信绑定响应。"""
    message: str
    openid: str
