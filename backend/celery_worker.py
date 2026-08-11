"""
backend/celery_worker.py

职责: Celery Worker 启动入口，创建 app 实例并自动发现 app/tasks/ 下的任务模块
分工: 🟢 弱模型可独立完成
对应日程: Day 5（随知识库异步任务一起搭建）
"""

from celery import Celery
from celery.schedules import crontab

celery_app = Celery(
    "zhihub",
    broker="amqp://guest:guest@localhost:5672/",
    backend=None,
)

celery_app.autodiscover_tasks(["app.tasks"])

# 计费定时调度（T20）: 与 main.py 的 _run_billing_scheduler 同款 crontab——
# demo 直接跑应用用线程调度；生产用 Celery Beat 部署时此配置生效
celery_app.conf.beat_schedule = {
    "daily-usage-aggregation": {
        "task": "app.tasks.billing_tasks.aggregate_daily_usage",
        "schedule": crontab(hour=2, minute=0),
    },
    "monthly-invoice-generation": {
        "task": "app.tasks.billing_tasks.generate_monthly_invoices",
        "schedule": crontab(day_of_month=1, hour=3, minute=0),
    },
}
