"""
backend/app/api/v1/session.py

职责: 会话生命周期全流程 -- 创建/列表/详情、用户发消息驱动 Agent、SSE 流式输出、
      转人工（坐席认领/退回）、留言建单、Trace 查询、CSAT 满意度评价

端点概览:
  POST /                              创建会话                    -> session_service.create_session
  GET  /list                          会话列表（分页 + 筛选）      -> session_crud.list_sessions
  GET  /{session_id}                  会话详情（含消息列表）        -> session_service.get_session_detail
  POST /message                       发送消息驱动 Agent            -> agent_service.handle_message
  POST /message/stream                SSE 流式输出                  -> agent_service.stream_message_events
  POST /{session_id}/claim            坐席认领（Redis SETNX 锁）    -> human_service.claim_session
  POST /{session_id}/return-to-agent  坐席退回给 AI                 -> human_service.return_to_agent
  GET  /{session_id}/trace            Agent 执行轨迹（MongoDB）     -> trace_service.get_trace_by_session
  POST /{session_id}/leave-message    转人工无坐席时留言建单        -> human_service.leave_message_if_no_agent
  POST /{session_id}/csat             CSAT 满意度评价（closed 后）  -> session_service.submit_csat

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/session
  - 下游: session_service / agent_service / human_service / trace_service / rate_limit_service
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession
import json
import logging

from app.api.deps import get_db, require_role
from app.schemas.session import (
    AgentMessageRequest,
    CSATRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    FeedbackRequest,
    FeedbackResponse,
    LeaveMessageRequest,
    QuickReplyCreateRequest,
    QuickReplyItem,
    SendMessageRequest,
    SendMessageResponse,
    SessionDetailResponse,
    SessionListResponse,
)
from app.services import session_service
from app.services import tenant_service

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_tenant_id(request: Request) -> int:
    """从 request.state 提取 tenant_id，未认证 -> 401。"""
    tid = getattr(request.state, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=401, detail="未认证")
    return tid


# ─── POST /session（创建）──
@router.post("", response_model=CreateSessionResponse, status_code=201)
def create_session(
    request: Request,
    body: CreateSessionRequest,
    db: DbSession = Depends(get_db),
):
    """创建新会话: 调 session_service.create_session。"""
    tenant_id = _get_tenant_id(request)
    try:
        result = session_service.create_session(db, tenant_id=tenant_id, end_user_id=body.end_user_id)
    except tenant_service.TenantInactive as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except tenant_service.TenantNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return result


# ─── GET /session/list ───
@router.get("/list", response_model=SessionListResponse)
def list_sessions(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    sentiment_filter: str | None = Query(None, alias="sentiment"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: DbSession = Depends(get_db),
):
    """会话列表（分页 + 状态/情绪筛选）: 调 session_crud.list_sessions。"""
    tenant_id = _get_tenant_id(request)
    from app.crud import session as session_crud

    items, total = session_crud.list_sessions(
        db, tenant_id, status=status_filter, sentiment=sentiment_filter,
        page=page, page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ─── 快捷回复（PRD §3.7）──
# 注意: /quick-replies 是单段路径，必须注册在 /{session_id} 之前，
# 否则会被 /{session_id}(int) 路由拦截返回 422（"quick-replies" 无法转 int）。
@router.get("/quick-replies", response_model=list[QuickReplyItem])
def list_quick_replies(
    request: Request,
    db: DbSession = Depends(get_db),
):
    """快捷回复模板列表: 按租户隔离，供坐席工作台一键选用。"""
    from app.crud.quick_reply import list_by_tenant

    tenant_id = _get_tenant_id(request)
    items = list_by_tenant(db, tenant_id)
    return [
        QuickReplyItem(id=q.id, content=q.content, shortcut=q.shortcut) for q in items
    ]


@router.post("/quick-replies", response_model=QuickReplyItem, status_code=201)
def create_quick_reply(
    body: QuickReplyCreateRequest,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """创建快捷回复模板: admin/agent 可建，按租户隔离。调 session_service.create_quick_reply。"""
    tenant_id = _get_tenant_id(request)
    return session_service.create_quick_reply(
        db, tenant_id=tenant_id, content=body.content, shortcut=body.shortcut
    )


# ─── GET /session/{id} ───
@router.get(
    "/{session_id}/business-context",
    dependencies=[Depends(require_role("admin", "agent"))],
)
def get_business_context(
    session_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """坐席侧业务上下文：只解析会话中明确出现的订单/工单编号。"""
    try:
        return session_service.get_session_business_context(
            db,
            session_id=session_id,
            tenant_id=_get_tenant_id(request),
        )
    except session_service.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(
    session_id: int,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """会话详情（含消息列表）: 调 session_service.get_session_detail，不存在 -> 404。"""
    tenant_id = _get_tenant_id(request)
    try:
        return session_service.get_session_detail(db, session_id=session_id, tenant_id=tenant_id)
    except session_service.SessionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


# ─── POST /session/message ───
@router.post("/message", response_model=SendMessageResponse)
def send_message(
    body: SendMessageRequest,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """发送用户消息驱动 Agent: 先限流（10 次/分钟），再调 agent_service.handle_message。"""
    tenant_id = _get_tenant_id(request)

    from app.crud import session as session_crud
    session = session_crud.get_by_id(db, body.session_id, tenant_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {body.session_id}")
    try:
        session_service.ensure_agent_execution_allowed(session)
    except session_service.SessionHumanControlled as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # 规格 007 US4: 终端用户对话消息限流（10 次/分钟，超限 429 不落库不计轮次）
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        from app.services.rate_limit_service import check_message_rate_limit
        if not check_message_rate_limit(user_id)["allowed"]:
            raise HTTPException(status_code=429, detail="您提问过于频繁，请稍后再试")

    # 导入 agent_service（懒加载，避免循环依赖）
    from app.services.agent_service import handle_message

    try:
        result = handle_message(
            db=db,
            session_id=body.session_id,
            tenant_id=tenant_id,
            user_message=body.content,
        )
        return result
    except session_service.SessionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except session_service.SessionHumanControlled as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        if isinstance(e, tenant_service.TenantInactive):
            raise HTTPException(status_code=403, detail=str(e)) from e
        # 不向客户端泄露内部异常原文（服务端日志保留）
        import logging
        logging.getLogger(__name__).exception("Agent handle_message failed: session=%s", body.session_id)
        raise HTTPException(status_code=500, detail="Agent 处理失败，请稍后重试") from e

@router.post("/message/stream")
async def send_message_stream(body: SendMessageRequest, request: Request, db: DbSession = Depends(get_db)):
    """SSE 流式输出端点 -- 真 token 级流式（规格 010 US2 / 架构完善）。

    流式端点复用 Agent 主链路能力（注入检测/RAG/安全/计费/trace）:
      prepare_message_stream: 加载上下文 + 注入检测 + graph.invoke 获取路由/检索结果
      llm_complete_stream:    基于检索结果逐 token 流式生成回答（litellm stream=True）
      finalize_message_stream: 保存 assistant 消息 + 计费 + trace + 记忆抽取
    """
    from app.crud import session as session_crud
    from app.services.agent_service import stream_message_events

    tenant_id = _get_tenant_id(request)
    # PRD §3.4/4.1：stream 端点同样受终端用户级限流（10 次/分钟）
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        from app.services.rate_limit_service import check_message_rate_limit

        if not check_message_rate_limit(user_id)["allowed"]:
            raise HTTPException(status_code=429, detail="您提问过于频繁，请稍后再试")
    session = session_crud.get_by_id(db, body.session_id, tenant_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {body.session_id}")
    try:
        session_service.ensure_agent_execution_allowed(session)
    except session_service.SessionHumanControlled as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    async def generate():
        try:
            async for event in stream_message_events(
                db,
                session_id=body.session_id,
                tenant_id=tenant_id,
                user_message=body.content,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception:
            logger.exception("Session stream failed: session=%s", body.session_id)
            yield f"data: {json.dumps({'error': '流式处理失败，请稍后重试'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post(
    "/{session_id}/agent-message",
    response_model=SendMessageResponse,
    dependencies=[Depends(require_role("agent", "admin"))],
)
def send_agent_message(
    session_id: int,
    body: AgentMessageRequest,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """当前认领客服回复人工接管会话；不会进入 Agent 执行链路。"""
    from app.services.human_service import (
        AgentMessageForbidden,
        AgentMessageStateInvalid,
        HumanHandoffError,
        send_agent_message as do_send,
    )

    tenant_id = _get_tenant_id(request)
    agent_id = getattr(request.state, "user_id", 0)
    try:
        return do_send(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            content=body.content,
        )
    except AgentMessageForbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except AgentMessageStateInvalid as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except HumanHandoffError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{session_id}/claim")
def claim_session(session_id: int, request: Request, db: DbSession = Depends(get_db)):
    """坐席认领会话: 调 human_service.claim_session（Redis SETNX 分布式锁，双坐席同时认领仅一人成功）。"""
    tenant_id = _get_tenant_id(request)
    agent_id = getattr(request.state, "user_id", 0)
    from app.services.human_service import claim_session as do_claim, HumanHandoffError
    try:
        # 原子认领: SETNX + 状态校验 + 更新 assignee_id
        return do_claim(db, session_id=session_id, agent_id=agent_id, tenant_id=tenant_id)
    except HumanHandoffError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{session_id}/return-to-agent")
def return_session(session_id: int, request: Request, db: DbSession = Depends(get_db)):
    """坐席退回会话给 AI Agent: 调 human_service.return_to_agent（释放认领锁 + 恢复 agent_handling）。"""
    tenant_id = _get_tenant_id(request)
    agent_id = getattr(request.state, "user_id", 0)
    from app.services.human_service import return_to_agent as do_return, HumanHandoffError
    try:
        return do_return(db, session_id=session_id, tenant_id=tenant_id, agent_id=agent_id)
    except HumanHandoffError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/trace")
async def get_session_trace(session_id: int, request: Request, db: DbSession = Depends(get_db)):
    """获取会话 Agent 执行轨迹（MongoDB trace）: 先校验会话归属再查 trace，防跨租户枚举。"""
    from app.services.trace_service import get_trace_by_session
    from app.crud import session as session_crud
    tenant_id = _get_tenant_id(request)
    if session_crud.get_by_id(db, session_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    traces = await get_trace_by_session(session_id)
    return {"session_id": session_id, "traces": traces, "total": len(traces)}


# ─── 规格 007 US3: 留言建单 + CSAT 满意度评价 ───
@router.post("/{session_id}/leave-message", status_code=201)
def leave_message(
    session_id: int,
    body: LeaveMessageRequest,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """转人工无在线坐席时留言自动建单（category=message）。

    流程:
      1. 校验会话存在且为 waiting_human 状态
      2. 有在线坐席 -> 409（等待认领，无需留言）
      3. 无在线坐席 -> 调 human_service.leave_message_if_no_agent 建留言工单
    """
    from app.services.human_service import leave_message_if_no_agent
    from app.services.session_service import has_online_agent
    from app.crud import session as session_crud

    tenant_id = _get_tenant_id(request)
    s = session_crud.get_by_id(db, session_id, tenant_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    if s.status != "waiting_human":
        raise HTTPException(status_code=403, detail="会话非转人工状态，无需留言")
    if has_online_agent(tenant_id):
        raise HTTPException(status_code=409, detail="当前有在线坐席，请等待认领")

    result = leave_message_if_no_agent(
        db, session_id=session_id, tenant_id=tenant_id, user_message=body.content,
    )
    return result


@router.post("/{session_id}/csat", status_code=201)
def submit_csat(
    session_id: int,
    body: CSATRequest,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """会话 CLOSED 后提交 CSAT 满意度评价（1-5 星 + 可选文字）。同一会话仅一次。

    调 session_service.submit_csat（service 内做存在/状态/去重校验 + commit）。
    """
    tenant_id = _get_tenant_id(request)
    try:
        return session_service.submit_csat(
            db, session_id=session_id, tenant_id=tenant_id,
            score=body.score, comment=body.comment,
        )
    except session_service.SessionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except session_service.SessionNotClosed as e:
        raise HTTPException(status_code=403, detail=str(e))
    except session_service.CSATAlreadyExists as e:
        raise HTTPException(status_code=409, detail=str(e))


# ─── 回答反馈（PRD §3.8）──
@router.post("/message/{message_id}/feedback", response_model=FeedbackResponse, status_code=201)
def submit_feedback(
    message_id: int,
    body: FeedbackRequest,
    request: Request,
    db: DbSession = Depends(get_db),
):
    """对单条 Agent 回复提交 👍/👎 反馈。

    流程（在 session_service.submit_feedback 内）:
      1. score=0 -> 422（schema 允许 0 但业务判非法）
      2. 校验消息存在且属于当前租户（message -> session.tenant_id 间接隔离）
      3. 同一消息已有反馈 -> 409
      4. score=-1 时 bad_case=True 标记低质回答待复盘
    """
    tenant_id = _get_tenant_id(request)
    try:
        return session_service.submit_feedback(
            db, message_id=message_id, tenant_id=tenant_id,
            score=body.score, comment=body.comment,
        )
    except session_service.InvalidFeedbackScore as e:
        raise HTTPException(status_code=422, detail=str(e))
    except session_service.MessageNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except session_service.FeedbackAlreadyExists as e:
        raise HTTPException(status_code=409, detail=str(e))
