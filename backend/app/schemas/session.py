"""
backend/app/schemas/session.py

职责: 会话/消息 API 请求与响应 Pydantic 模型
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ---------- 会话创建 ----------
class CreateSessionRequest(BaseModel):
    """创建会话请求（可带首条消息 + 终端用户标识）。"""
    initial_message: str = Field(default="", max_length=2000)
    end_user_id: str | None = Field(default=None, max_length=64)


class CreateSessionResponse(BaseModel):
    """创建会话响应：会话 ID、初始状态、创建时间。"""
    session_id: int
    status: str
    created_at: datetime


# ---------- 发送消息 ----------
class SendMessageRequest(BaseModel):
    """发送消息请求：会话 ID + 消息内容。"""
    session_id: int
    content: str = Field(..., min_length=1, max_length=2000)


class SourceItem(BaseModel):
    """检索引用来源项：文档名、切片序号、命中文本。"""
    doc: str
    chunk_index: int
    text: str


class MessageMetadata(BaseModel):
    """消息附加指标：意图、置信度、情绪、耗时、token 数。"""
    intent: str | None = None
    confidence: float | None = None
    sentiment: str | None = None
    elapsed_ms: int | None = None
    tokens: int | None = None
    degraded: bool | None = None  # T19: 降级模式标记（账单逾期），前端据此提示用户


class SendMessageResponse(BaseModel):
    """发送消息响应：消息 ID、角色、内容、引用来源、附加指标。"""
    # message_id 可空：降级模式不落库（不调 LLM 不写消息），无 message_id；
    # 正常路径恒为非空 int
    message_id: int | None = None
    role: str
    content: str
    sources: list[SourceItem] = Field(default_factory=list)
    metadata: MessageMetadata | None = None


# ---------- 会话列表 ----------
class SessionItem(BaseModel):
    """会话列表项：状态、意图、情绪、消息数、时间。"""
    session_id: int
    status: str
    intent: str | None = None
    sentiment: str | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SessionListResponse(BaseModel):
    """会话列表分页响应。"""
    items: list[SessionItem]
    total: int
    page: int
    page_size: int


# ---------- 会话详情 ----------
class MessageItem(BaseModel):
    """消息项：消息 ID、角色、内容、引用来源、时间。"""
    message_id: int
    role: str
    content: str
    sources: list[SourceItem] | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class SessionDetailResponse(BaseModel):
    """会话详情响应：状态、意图、情绪、消息列表。"""
    session_id: int
    status: str
    intent: str | None = None
    sentiment: str | None = None
    message_count: int = 0
    created_at: datetime | None = None
    # T28: 前端详情页渲染时间用（此前缺字段导致前端 Invalid Date）
    updated_at: datetime | None = None
    messages: list[MessageItem] = Field(default_factory=list)


# ---------- 留言建单（007-session-lifecycle US3）----------
class LeaveMessageRequest(BaseModel):
    """留言建单请求：留言内容（会话关闭后转工单）。"""
    content: str = Field(..., min_length=1, max_length=2000)


class LeaveMessageResponse(BaseModel):
    """留言建单响应：生成的工单号 + 原会话 ID。"""
    ticket_no: str
    session_id: int


# ---------- CSAT 满意度评价（007-session-lifecycle US3）----------
class CSATRequest(BaseModel):
    """满意度评价请求：1-5 分 + 可选文字评价。"""
    score: int = Field(..., ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)


class CSATResponse(BaseModel):
    """满意度评价响应：评价 ID、会话 ID、分数。"""
    id: int
    session_id: int
    score: int


# ---------- 回答反馈（021 PRD 3.8）----------
class FeedbackRequest(BaseModel):
    """回答反馈请求：1=👍 / -1=👎 + 可选文字原因。"""
    score: int = Field(..., ge=-1, le=1, description="1=有用 / -1=无用")
    comment: str | None = Field(default=None, max_length=500)


class FeedbackResponse(BaseModel):
    """回答反馈响应：反馈 ID、消息 ID、评分、bad_case 标记。"""
    id: int
    message_id: int
    score: int
    bad_case: bool


# ---------- 快捷回复（022 PRD 3.7）----------
class QuickReplyCreateRequest(BaseModel):
    """快捷回复创建请求：模板内容 + 可选快捷指令。"""
    content: str = Field(..., min_length=1, max_length=2000)
    shortcut: str | None = Field(default=None, max_length=50)


class QuickReplyItem(BaseModel):
    """快捷回复列表项。"""
    id: int
    content: str
    shortcut: str | None = None
