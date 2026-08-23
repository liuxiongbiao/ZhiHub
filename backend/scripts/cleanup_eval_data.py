"""
backend/scripts/cleanup_eval_data.py

职责: 清理评测/冒烟产生的测试数据（审计与阈值标定实验的残留）。

清理目标:
  1. eval-sessions   tenant 1 下 end_user_id 形如 eval-* 的评测会话及其消息/反馈/CSAT/trace/长期记忆
  2. smoke-tenant    审计冒烟租户（tenant_id=75，audit.smoke@test.local）的全量数据
  3. seeded-kb       （默认关闭）tenant 1 由 seed_knowledge.py 灌入的知识库文档与切片
                     —— 保留原因：demo 租户知识库是演示/答辩资产，如需清空再加该参数

用法（默认 dry-run，只打印将删除的统计；确认后加 --execute 真正执行）:
  cd backend && .venv/Scripts/python.exe scripts/cleanup_eval_data.py
  .venv/Scripts/python.exe scripts/cleanup_eval_data.py --execute
  .venv/Scripts/python.exe scripts/cleanup_eval_data.py --execute --seeded-kb

前置: 本机可连通 MySQL/Mongo/Milvus/ES（backend/.env 已配置 localhost）
"""
import argparse
import sys
from collections import OrderedDict

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])

from app.config import settings
from app.core.database import SessionLocal
from app.core.embedding_client import get_es_client, get_memory_collection, get_milvus_collection
from app.models import (
    answer_feedback,
    audit_log,
    billing_event,
    billing_invoice,
    csat,
    knowledge_document,
    message,
    notification,
    order,
    quick_reply,
    refund_approval,
    refund_approval_event,
    refund_outbox,
    refresh_token,
    security_incident,
    session,
    tenant,
    tenant_member,
    ticket,
    usage_record,
    user,
)

SMOKE_TENANT_ID = 75
SMOKE_EMAIL = "audit.smoke@test.local"
SEED_DOC_IDS = list(range(125, 149))  # seed_knowledge.py 2026-08-23 产出 doc_id 125-148

# 按关联键类型分组的删除清单（顺序即 FK 依赖顺序）
TABLES_BY_TENANT = [
    ("tickets", ticket.Ticket),
    ("notifications", notification.Notification),
    ("quick_replies", quick_reply.QuickReply),
    ("usage_records", usage_record.UsageRecord),
    ("billing_invoices", billing_invoice.BillingInvoice),
    ("billing_events", billing_event.BillingEvent),
    ("security_incidents", security_incident.SecurityIncident),
    ("audit_logs", audit_log.AuditLog),
    ("knowledge_documents", knowledge_document.KnowledgeDocument),
    ("refresh_tokens", refresh_token.RefreshToken),
    ("tenant_members", tenant_member.TenantMember),
]
TABLES_BY_ORDER = [
    ("refund_approval_events", refund_approval_event.RefundApprovalEvent),
    ("refund_outbox", refund_outbox.RefundOutbox),
    ("refund_approvals", refund_approval.RefundApproval),
]
TABLES_BY_SESSION = [
    ("csat_evaluations", csat.CsatEvaluation),
    ("answer_feedback", answer_feedback.AnswerFeedback),
    ("messages", message.Message),
]


def _tenant_session_ids(db, tenant_id, end_user_prefix=None):
    q = db.query(session.Session).filter(session.Session.tenant_id == tenant_id)
    if end_user_prefix:
        q = q.filter(session.Session.end_user_id.like(end_user_prefix + "%"))
    return [s.id for s in q.all()]


def _clean_mongo_traces(db, session_ids, execute):
    if not session_ids:
        return 0
    if not execute:
        from pymongo import MongoClient

        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
        col = client.get_default_database()["agent_traces"]
        n = col.count_documents({"session_id": {"$in": session_ids}})
        client.close()
        return n
    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    col = client.get_default_database()["agent_traces"]
    res = col.delete_many({"session_id": {"$in": session_ids}})
    client.close()
    return res.deleted_count


def _purge_sessions(db, session_ids, execute):
    stats = OrderedDict()
    msg_ids = [
        m.id
        for m in db.query(message.Message).filter(message.Message.session_id.in_(session_ids)).all()
    ]
    for label, model, attr, ids in [
        ("csat", csat.CsatEvaluation, csat.CsatEvaluation.session_id, session_ids),
        ("answer_feedback", answer_feedback.AnswerFeedback, answer_feedback.AnswerFeedback.message_id, msg_ids),
        ("messages", message.Message, message.Message.session_id, session_ids),
    ]:
        stats[label] = db.query(model).filter(attr.in_(ids)).count() if ids else 0
        if execute and ids:
            db.query(model).filter(attr.in_(ids)).delete(synchronize_session=False)
    stats["sessions"] = len(session_ids)
    if execute and session_ids:
        db.query(session.Session).filter(session.Session.id.in_(session_ids)).delete(synchronize_session=False)
    stats["mongo_traces"] = _clean_mongo_traces(db, session_ids, execute)
    return stats


def _purge_smoke_tenant(db, execute):
    stats = OrderedDict()
    order_ids = [o.id for o in db.query(order.Order).filter(order.Order.tenant_id == SMOKE_TENANT_ID).all()]
    session_ids = _tenant_session_ids(db, SMOKE_TENANT_ID)
    msg_ids = [
        m.id for m in db.query(message.Message).filter(message.Message.session_id.in_(session_ids)).all()
    ] if session_ids else []

    for label, model in TABLES_BY_TENANT:
        stats[label] = db.query(model).filter(model.tenant_id == SMOKE_TENANT_ID).count()
        if execute:
            db.query(model).filter(model.tenant_id == SMOKE_TENANT_ID).delete(synchronize_session=False)
    for label, model in TABLES_BY_ORDER:
        stats[label] = db.query(model).filter(model.order_id.in_(order_ids)).count() if order_ids else 0
        if execute and order_ids:
            db.query(model).filter(model.order_id.in_(order_ids)).delete(synchronize_session=False)
    stats["orders"] = len(order_ids)
    if execute and order_ids:
        db.query(order.Order).filter(order.Order.id.in_(order_ids)).delete(synchronize_session=False)

    stats.update(_purge_sessions(db, session_ids, execute))

    u = db.query(user.User).filter(user.User.email == SMOKE_EMAIL).first()
    stats["refresh_tokens"] = 0
    if u:
        stats["refresh_tokens"] = db.query(refresh_token.RefreshToken).filter(
            refresh_token.RefreshToken.user_id == u.id
        ).count()
        if execute:
            db.query(refresh_token.RefreshToken).filter(
                refresh_token.RefreshToken.user_id == u.id
            ).delete(synchronize_session=False)
            db.delete(u)
        stats["users"] = 1
    t = db.query(tenant.Tenant).filter(tenant.Tenant.id == SMOKE_TENANT_ID).first()
    stats["tenants"] = 1 if t else 0
    if execute and t:
        db.delete(t)
    return stats


def _clean_es(tenant_ids=None, doc_ids=None):
    es = get_es_client()
    must = []
    if tenant_ids:
        must.append({"terms": {"tenant_id": list(tenant_ids)}})
    if doc_ids:
        must.append({"terms": {"doc_id": list(doc_ids)}})
    query = {"bool": {"must": must}} if must else {"match_all": {}}
    res = es.delete_by_query(index="knowledge_chunks", query=query, conflicts="proceed", refresh=True)
    return res.get("deleted", 0)


def main():
    parser = argparse.ArgumentParser(description="清理评测/冒烟数据")
    parser.add_argument("--execute", action="store_true", help="真正删除（默认 dry-run 只统计）")
    parser.add_argument("--no-eval-sessions", action="store_true", help="跳过 tenant 1 的 eval-* 评测会话")
    parser.add_argument("--no-smoke-tenant", action="store_true", help="跳过审计冒烟租户（tenant 75）")
    parser.add_argument("--seeded-kb", action="store_true", help="同时清空 tenant 1 的 seed 知识库（默认保留）")
    args = parser.parse_args()

    db = SessionLocal()
    print(f"=== cleanup_eval_data [{ 'EXECUTE' if args.execute else 'DRY-RUN' }] ===")

    # 1) eval-* 评测会话（tenant 1）
    if not args.no_eval_sessions:
        sids = _tenant_session_ids(db, 1, end_user_prefix="eval-")
        stats = _purge_sessions(db, sids, args.execute)
        print(f"[eval-sessions] 会话 {len(sids)} 个 -> {dict(stats)}")
        if args.execute and sids:
            get_memory_collection().delete(expr='user_id like "eval-%"')
            print("[eval-memory] 已删除 Milvus user_memories 中 user_id=eval-* 的长期记忆")
        elif sids:
            print("[eval-memory] DRY-RUN: 将删除 Milvus user_memories 中 user_id=eval-* 的长期记忆")
    else:
        print("[eval-sessions] skipped")

    # 2) 冒烟租户 tenant 75
    if not args.no_smoke_tenant:
        stats = _purge_smoke_tenant(db, args.execute)
        print(f"[smoke-tenant] tenant {SMOKE_TENANT_ID} -> {dict(stats)}")
        if args.execute:
            get_milvus_collection().delete(expr=f"tenant_id == {SMOKE_TENANT_ID}")
            get_memory_collection().delete(expr=f"tenant_id == {SMOKE_TENANT_ID}")
            n_es = _clean_es(tenant_ids=[SMOKE_TENANT_ID])
            print(f"[smoke-vector] Milvus/ES 切片已清理（ES deleted={n_es}）")
        else:
            print("[smoke-vector] DRY-RUN: 将删除 Milvus/ES 中 tenant_id=75 的全部切片")
    else:
        print("[smoke-tenant] skipped")

    # 3) 可选：清空 tenant 1 seed 知识库
    if args.seeded_kb:
        if args.execute:
            db.query(knowledge_document.KnowledgeDocument).filter(
                knowledge_document.KnowledgeDocument.id.in_(SEED_DOC_IDS)
            ).delete(synchronize_session=False)
            get_milvus_collection().delete(expr=f"doc_id in {SEED_DOC_IDS}")
            n_es = _clean_es(doc_ids=SEED_DOC_IDS)
            print(f"[seeded-kb] 已删除 tenant 1 seed 文档 doc_id {SEED_DOC_IDS[0]}-{SEED_DOC_IDS[-1]}（ES deleted={n_es}）")
        else:
            print(f"[seeded-kb] DRY-RUN: 将删除 tenant 1 seed 文档 doc_id 125-148 及其 Milvus/ES 切片")
    else:
        print("[seeded-kb] skipped（demo 知识库保留，如需清空加 --seeded-kb）")

    if args.execute:
        db.commit()
        print("=== 已提交删除 ===")
    else:
        db.rollback()
        print("=== DRY-RUN 完成：未做任何修改；确认无误后加 --execute 执行 ===")
    db.close()


if __name__ == "__main__":
    main()
