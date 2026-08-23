"""
backend/celery_worker.py

职责: Celery Worker 启动入口，创建 app 实例并自动发现 app/tasks/ 下的任务模块
"""

from celery import Celery
from celery.schedules import crontab

# broker 从统一配置读取（避免与 docker-compose 注入的 RABBITMQ_URL 不一致）
from app.config import settings

celery_app = Celery(
    "zhihub",
    broker=settings.rabbitmq_url,
    backend=None,
)

# 显式导入任务模块，确保 worker 注册全部任务（include/autodiscover 在 Windows+solo 下不可靠）
import app.tasks.deep_agent_tasks  # noqa: F401
import app.tasks.billing_tasks  # noqa: F401
import app.tasks.cleanup_tasks  # noqa: F401
import app.tasks.knowledge_tasks  # noqa: F401
import app.tasks.notification_tasks  # noqa: F401

# 计费定时调度: 与 main.py 的 _run_billing_scheduler 同款 crontab——
# demo 直接跑应用用线程调度；生产用 Celery Beat 部署时此配置生效
celery_app.conf.beat_schedule = {
    "daily-usage-aggregation": {
        "task": "app.tasks.billing_tasks.aggregate_daily_usage",
        "schedule": crontab(hour=2, minute=0),
        "options": {"queue": "maintenance"},
    },
    "monthly-invoice-generation": {
        "task": "app.tasks.billing_tasks.generate_monthly_invoices",
        "schedule": crontab(day_of_month=1, hour=3, minute=0),
        "options": {"queue": "maintenance"},
    },
    "periodic-maintenance": {
        "task": "app.tasks.cleanup_tasks.run_periodic_maintenance",
        "schedule": 300.0,
        "options": {"queue": "maintenance"},
    },
}

celery_app.conf.task_routes = {
    "app.tasks.knowledge_tasks.process_document": {"queue": "knowledge"},
    "app.tasks.cleanup_tasks.run_periodic_maintenance": {"queue": "maintenance"},
    "app.tasks.billing_tasks.*": {"queue": "maintenance"},
}
