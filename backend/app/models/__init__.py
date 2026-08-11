"""
backend/app/models/__init__.py

职责: 汇总导入所有 ORM 模型，确保 Base.metadata 收集到全部表定义（alembic autogenerate依赖此文件）
分工: 🟢 弱模型可独立完成
对应日程: Day 1
"""

# 必须在这里把每个模型都 import 一遍——即使代码里没有直接用到
# 因为 alembic/env.py 只 import 了 Base，只有当每个模型类被 import 执行过，
# 它们才会往 Base.metadata 里注册自己的表结构，autogenerate 才能发现新表
from app.models.answer_feedback import AnswerFeedback
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.billing_invoice import BillingInvoice
from app.models.csat import CsatEvaluation
from app.models.knowledge_document import KnowledgeDocument
from app.models.message import Message
from app.models.order import Order
from app.models.quick_reply import QuickReply
from app.models.refund_approval import RefundApproval
from app.models.security_incident import SecurityIncident
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.tenant_member import TenantMember
from app.models.ticket import Ticket
from app.models.usage_record import UsageRecord
from app.models.user import User

__all__ = ["AnswerFeedback", "AuditLog", "Base", "BillingInvoice", "CsatEvaluation", "KnowledgeDocument", "Message", "Order", "QuickReply", "RefundApproval", "SecurityIncident", "Session", "Tenant", "TenantMember", "Ticket", "UsageRecord", "User"]
