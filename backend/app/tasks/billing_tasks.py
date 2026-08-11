"""
backend/app/tasks/billing_tasks.py

职责: 每日定时聚合 Redis 配额计数器落库至 usage_records、月初生成账单
分工: 🟢 弱模型可独立完成
对应日程: Day 6 / 005-billing

核心设计:
  Celery Beat 定时触发: 每天 02:00 UTC 聚合用量，每月 1 号 03:00 UTC 生成账单。
  Redis 计数器（billing:quota:{tenant_id}）实时记录对话量，定时落库持久化。

关键技术点:
  - Redis 计数器 + 定时落库: 实时计数用 Redis INCR（高性能），持久化用定时任务批量落库。
    避免每次对话都写 DB，减轻数据库压力。
  - scan_iter 而非 keys: SCAN 命令非阻塞迭代，避免 KEYS 阻塞 Redis（大 key 空间时）。
  - 月初账单: 遍历所有租户，调用 generate_invoice 生成上月账单。
    异常时整体 rollback（all-or-nothing），保证账单一致性。

⚠️ 潜在问题:
  - 此模块可能未被实际调度（需确认 Celery Beat 配置是否注册）。

定时触发: Celery Beat 每天凌晨 2 点执行 aggregate_daily_usage()，
          每月1号凌晨3点执行 generate_monthly_invoices()。

依赖关系:
  - 上游: Celery Beat（定时调度）
  - 下游: app.core.redis、app.core.database、app.services.billing_service、
          app.models.usage_record/tenant
"""

import logging
from datetime import date, datetime, timezone
from celery import shared_task

from app.core.database import SessionLocal
from app.core.redis import get_redis
from app.services.billing_service import TRIAL_QUOTA

logger = logging.getLogger(__name__)


@shared_task
def aggregate_daily_usage() -> dict:
    """每日聚合：将各租户的 Redis 配额计数器落库到 usage_records 表。

    流程:
      1. scan_iter 扫描所有 billing:quota:* 键
      2. 解析 tenant_id，读取累计计数（INCRBYFLOAT 写入，需 float 解析取整）
      3. 与上次聚合快照对比得出当日增量；查询当日是否已有记录（去重）
      4. 无记录则 INSERT usage_records（当日增量 + is_overage 标记），并更新快照

    Celery Beat schedule: 每天 02:00 UTC

    Returns:
        {"status": "ok", "tenants": N, "date": str} 或 {"status": "error", "error": str}
    """
    from app.models.usage_record import UsageRecord

    db = SessionLocal()
    try:
        r = get_redis()
        today = datetime.now(timezone.utc).date()
        count = 0

        month = today.strftime("%Y-%m")

        # 扫描所有 billing:quota:* 键（SCAN 非阻塞，避免 KEYS 阻塞 Redis）
        for key in r.scan_iter("billing:quota:*"):
            # key 格式 billing:quota:{tenant_id}:{YYYY-MM}；只处理当月 key。
            # 跨月时上月 key 因 40 天 TTL 仍存在，若不过滤会用上月累计值对比当月快照，
            # 当日增量被污染（上月全月累计被误记为今日增量，真增量因幂等命中丢失）。
            parts = key.split(":")
            if len(parts) < 4 or parts[3] != month:
                continue
            tenant_id = int(parts[2])

            # 当日增量 = 当前累计值 - 上次聚合快照值（首次聚合无快照按 0 起算）。
            # 计数器由 INCRBYFLOAT 写入（模型权重可为小数，如 0.5），必须 float 解析再取整；
            # 用 int(x+0.5) 而非 round()（银行家舍入会把 0.5 舍到偶，权重 0.5 的模型系统性丢零头）。
            cur_total = float(r.get(key) or 0)
            snapshot_key = f"billing:usage_last:{tenant_id}:{month}"
            last_total = float(r.get(snapshot_key) or 0)
            day_count = max(int(cur_total - last_total + 0.5), 0)  # max 防计数器过期后快照超前

            # 检查是否已记录（幂等，防止任务重复执行产生重复记录）
            from sqlalchemy import select
            existing = db.execute(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id, UsageRecord.date == today
                )
            ).scalar_one_or_none()

            if not existing:
                db.add(UsageRecord(
                    tenant_id=tenant_id, date=today,
                    conversation_count=day_count,
                    is_overage=cur_total >= TRIAL_QUOTA,
                ))
                # 仅插入成功路径更新快照；existing 命中（重复执行）不动快照，防止覆盖增量。
                # DB 与 Redis 双写无事务：commit 失败会丢该日增量、set 失败会致下日重复计入，
                # 均为罕见路径，可接受（与"预警失败不阻断计费"同级取舍）。
                try:
                    r.set(snapshot_key, cur_total, ex=60 * 60 * 24 * 60)
                except Exception:
                    logger.warning(f"Usage snapshot update failed for tenant {tenant_id} (non-blocking)")
                count += 1

        db.commit()
        logger.info(f"Daily usage aggregated: {count} tenants")
        return {"status": "ok", "tenants": count, "date": str(today)}
    except Exception as e:
        db.rollback()
        logger.exception(f"Daily aggregation failed: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


@shared_task
def generate_monthly_invoices() -> dict:
    """月初生成账单：为所有租户生成上月账单。

    流程:
      1. 计算上月 period（YYYY-MM 格式）
      2. 遍历所有租户，调用 generate_invoice 生成账单
      3. 整体 commit（all-or-nothing）

    Celery Beat schedule: 每月1号 03:00 UTC

    ⚠️ 潜在问题:
      - all-or-nothing: 单个租户账单生成失败会 rollback 所有，可能影响其他租户。
      - 无幂等检查: 若上月账单已存在，重复执行可能产生重复账单（取决于 generate_invoice 实现）。

    Returns:
        {"status": "ok", "tenants": N, "period": str} 或 {"status": "error", "error": str}
    """
    from app.models.tenant import Tenant
    from app.services.billing_service import generate_invoice
    from sqlalchemy import select

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 上月: 当前月-1（1月则回退到上年12月）
        month = now.month - 1 if now.month > 1 else 12
        year = now.year if now.month > 1 else now.year - 1
        period = f"{year}-{month:02d}"

        tenants = db.execute(select(Tenant)).scalars().all()
        count = 0
        for tenant in tenants:
            generate_invoice(db, tenant_id=tenant.id, period=period)
            count += 1

        db.commit()
        logger.info(f"Monthly invoices generated: {count} tenants for {period}")
        return {"status": "ok", "tenants": count, "period": period}
    except Exception as e:
        db.rollback()
        logger.exception(f"Monthly invoice generation failed: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()
