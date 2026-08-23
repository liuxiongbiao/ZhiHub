"""
backend/app/main.py

职责: FastAPI 应用入口 -- 创建实例、注册中间件/路由/生命周期事件

核心设计:
  - 中间件链入站顺序: AccessLog -> Auth -> Tenant -> RateLimit（-> CORS -> 路由）
  - Starlette add_middleware 后注册者在外层先执行入站，故注册顺序需逆序写
  - lifespan 生命周期: 知识库消费者、建表兜底、空闲会话收尾三个 startup 任务

  注册代码顺序（从先到后）:
    1. CORSMiddleware   -- 最先注册 = 最内层，最后执行入站
    2. RateLimitMiddleware
    3. TenantMiddleware
    4. AuthMiddleware
    5. AccessLogMiddleware -- 最后注册 = 最外层，最先执行入站
  实际入站执行顺序: AccessLog -> Auth -> Tenant -> RateLimit -> CORS -> 路由

  为什么 Auth 必须在 Tenant 之前:
    Auth 解析 JWT -> 注入 request.state.token_tenant_id / user_id / role
    Tenant 从 request.state.token_tenant_id 搬运为 request.state.tenant_id
    RateLimit 依赖 request.state.tenant_id + user_id 做双维度限流
    若顺序反了，Tenant 读不到 token_tenant_id -> tenant_id 为空 -> 限流降级放行

  AccessLog 在最外层: 先注入 trace_id 再进 Auth，记录所有请求（含被 401 拒绝的）耗时。

  CORS 在最内层的原因:
    Auth 放行 OPTIONS 预检（不带 Authorization），CORS 在内层接管预检响应
    非预检请求经 Auth 鉴权后透传至 CORS 添加响应头，再进路由

依赖关系:
  - 上游: uvicorn/asgi server 加载本模块的 app 对象
  - 下游: app.api.v1.* 路由模块、app.middlewares.* 中间件、app.tasks.* 后台任务
"""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import get_db
from app.config import settings
from app.core.rabbitmq import consume_knowledge_tasks
from app.tasks.knowledge_tasks import process_document

from app.api.v1 import agent as agent_router
from app.api.v1 import auth as auth_router
from app.api.v1 import billing as billing_router
from app.api.v1 import knowledge as knowledge_router
from app.api.v1 import notification as notification_router
from app.api.v1.mcp import server as mcp_router
from app.api.v1 import order as order_router
from app.api.v1 import session as session_router
from app.api.v1 import tenant as tenant_router
from app.api.v1 import ticket as ticket_router
from app.api.v1 import widget as widget_router
from app.api.v1 import ws as ws_router
from app.middlewares.access_log_middleware import AccessLogMiddleware
from app.middlewares.auth_middleware import AuthMiddleware
from app.middlewares.csrf_middleware import CSRFMiddleware
from app.middlewares.rate_limit_middleware import RateLimitMiddleware
from app.middlewares.tenant_middleware import TenantMiddleware

logger = logging.getLogger(__name__)


# 知识库异步消费: 后台线程消费 RabbitMQ knowledge_processing 队列
def _run_knowledge_consumer() -> None:
    """后台消费知识库处理队列，崩溃后 5s 自动重连。"""
    while True:
        try:
            consume_knowledge_tasks(process_document)
        except Exception as e:
            logger.warning(f"Knowledge consumer crashed, retrying in 5s: {e}")
            time.sleep(5)


# 空闲会话定时收尾（规格 007）: 每 5min 跑一次
CLEANUP_INTERVAL_SECONDS = 300


def _billing_scheduler_tick(
    now,
    state: dict,
    *,
    aggregate_task=None,
    invoice_task=None,
) -> dict:
    """执行一次计费调度；只有任务明确成功才推进进程内标记。"""
    today = now.strftime("%Y-%m-%d")
    if now.strftime("%H:%M") >= "02:00" and state.get("last_usage_day") != today:
        if aggregate_task is None:
            from app.tasks.billing_tasks import aggregate_daily_usage

            aggregate_task = aggregate_daily_usage
        result = aggregate_task()
        if not isinstance(result, dict) or result.get("status") != "ok":
            error = result.get("error") if isinstance(result, dict) else None
            raise RuntimeError(error or "daily usage aggregation did not confirm success")
        state["last_usage_day"] = today

    month = now.strftime("%Y-%m")
    should_generate = now.day == 1 and now.strftime("%H:%M") >= "03:00"
    if should_generate and state.get("last_invoice_month") != month:
        if invoice_task is None:
            from app.tasks.billing_tasks import generate_monthly_invoices

            invoice_task = generate_monthly_invoices
        result = invoice_task()
        if not isinstance(result, dict) or result.get("status") != "ok":
            error = result.get("error") if isinstance(result, dict) else None
            raise RuntimeError(error or "monthly invoice generation did not confirm success")
        state["last_invoice_month"] = month
    return state


def _run_billing_scheduler() -> None:
    """计费定时调度: 每日 02:00 UTC 聚合用量 + 每月 1 号 03:00 UTC 生成账单。

    此前 billing_tasks 两个任务从未被任何调度注册（Celery beat 无配置），
    月度账单闭环缺失。demo 用 daemon 线程 + 轮询（与 idle_cleanup 同模式），
    生产可部署 celery_worker.py 的 beat_schedule（同款 crontab）。
    last_* 标记防同一窗口重复触发（进程内单实例）。
    """
    from datetime import datetime, timezone

    # 启动即视为"今日/本月已执行"（预置标记）：首 tick 不立即执行，
    # 从下一个跨天窗口才开始触发——避免服务启动瞬间（或测试环境 TestClient
    # lifespan）直接跑聚合写库
    startup_now = datetime.now(timezone.utc).replace(tzinfo=None)
    state = {
        "last_usage_day": startup_now.strftime("%Y-%m-%d"),
        "last_invoice_month": startup_now.strftime("%Y-%m"),
    }
    while True:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for attempt in range(3):
            try:
                _billing_scheduler_tick(now, state)
                break
            except Exception as e:
                logger.warning(f"Billing scheduler tick failed (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    time.sleep(5)
        time.sleep(60)


def _run_idle_cleanup() -> None:
    """周期性收尾: 空闲会话收尾 / 超期退款取消 / 订单超时流转 / 试用版降级只读。"""
    from app.core.database import SessionLocal
    from app.tasks.cleanup_tasks import (
        cleanup_agent_presence,
        cleanup_expired_docs,
        cleanup_expired_orders,
        cleanup_idle_sessions,
        cleanup_overdue_refunds,
        cleanup_trial_degradation,
        remind_expiring_docs,
    )

    while True:
        try:
            db = SessionLocal()
            try:
                cleanup_idle_sessions(db)
                cleanup_overdue_refunds(db)
                cleanup_expired_orders(db)
                cleanup_expired_docs(db)  # : 到期知识文档摘除（含检索库切片删除）
                cleanup_trial_degradation(db)
                cleanup_agent_presence()
                remind_expiring_docs(db)  # PRD 3.2: 到期前 7 天邮件提醒
                # 退款 Outbox 重试（外部退款失败后由后台任务补投）
                from app.services.order_service import process_refund_outbox_batch
                process_refund_outbox_batch(db)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Idle cleanup failed (non-blocking): {e}")
        time.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """API 生命周期只管理基础资源；业务消费与周期任务统一由 Celery 承担。"""
    # 建表兜底: alembic 未覆盖的表在此创建，已存在的跳过。失败不阻断启动
    try:
        from app.core.database import init_db
        init_db()
        logger.info("Database tables ensured")
    except Exception as e:
        logger.warning(f"init_db failed (non-blocking): {e}")

    from app.core.ws_manager import manager

    if settings.app_env != "test":
        await manager.start_pubsub()
        logger.info("WebSocket Redis Pub/Sub subscriber started")
    try:
        yield
    finally:
        if settings.app_env != "test":
            await manager.stop_pubsub()


app = FastAPI(title="ZhiHub API", version="0.1.0", lifespan=lifespan)

# CORS: 仅放开前端开发服务器 origin；allow_credentials=True 时规范不允许 allow_origins=["*"]
# CORS 来源从配置读取（逗号分隔 → 列表）
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 中间件注册（逆序）: 后注册者先执行入站
# 入站顺序 AccessLog → Auth → Tenant → RateLimit
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(AccessLogMiddleware)
# Widget 公共通道动态 CORS（最外层，最后注册先执行入站）—— 预检在鉴权链之前
# 按租户域名白名单应答；全局 CORSMiddleware 维持仅管理后台 origin，不放宽。
from app.middlewares.widget_cors_middleware import WidgetCorsMiddleware

app.add_middleware(WidgetCorsMiddleware)
# 审计复核：Prometheus 监控探针（最内层，先于业务中间件统计全量请求）
from app.middlewares.metrics_middleware import MetricsMiddleware

app.add_middleware(MetricsMiddleware)

# 路由挂载 —— 统一前缀 /api/v1/<module>
app.include_router(agent_router.router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(knowledge_router.router, prefix="/api/v1/knowledge", tags=["knowledge"])
app.include_router(billing_router.router, prefix="/api/v1/billing", tags=["billing"])
app.include_router(mcp_router.router, prefix="/api/v1/mcp", tags=["mcp"])
app.include_router(notification_router.router, prefix="/api/v1/notifications", tags=["notifications"])
app.include_router(order_router.router, prefix="/api/v1/order", tags=["order"])
app.include_router(session_router.router, prefix="/api/v1/session", tags=["session"])
app.include_router(tenant_router.router, prefix="/api/v1/tenant", tags=["tenant"])
app.include_router(ticket_router.router, prefix="/api/v1/ticket", tags=["ticket"])
app.include_router(widget_router.router, prefix="/api/v1/widget", tags=["widget"])
# WebSocket 坐席工作台（PRD §3.7）: prefix 拼出 /api/v1/ws/agent，WS 不走 HTTP 中间件链
app.include_router(ws_router.router, prefix="/api/v1/ws", tags=["websocket"])


# 健康检查: 供 docker / 负载均衡 / curl 探活
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/metrics")
def metrics_endpoint(db: Session = Depends(get_db)):
    """Prometheus 文本格式指标（审查  监控探针）。
    生产建议置于内网或加鉴权；demo 直接暴露便于抓取。"""
    from sqlalchemy import func, select
    from starlette.responses import Response
    from app.core.metrics import render_metrics, ACTIVE_SESSIONS
    from app.models.session import Session as SessionModel

    # 审计复核：抓取前刷新活跃会话 Gauge（waiting_human/human_handling/agent_handling）
    try:
        active = db.execute(
            select(func.count()).select_from(SessionModel).where(
                SessionModel.status.in_(["waiting_human", "human_handling", "agent_handling"])
            )
        ).scalar() or 0
        ACTIVE_SESSIONS.set(active)
    except Exception:
        ACTIVE_SESSIONS.set(0)

    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
