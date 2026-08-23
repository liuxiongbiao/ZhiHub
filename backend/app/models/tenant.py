"""
backend/app/models/tenant.py

职责: tenants 表 ORM 模型定义（租户套餐/配额/状态）

核心设计:
  多租户系统的核心隔离单位，每个企业客户对应一行，套餐决定配额与功能开关。
  关键字段: plan_type(trial/basic/pro/enterprise)、seat_limit(座席上限)、status(active/readonly/closed)、
            refund_approval_threshold(退款审批阈值，超限需人工审批)
  简化: 砍掉 widget 品牌定制字段、storage_quota_mb 精细存储上限。
"""

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


# 租户表：多租户系统的核心隔离单位，每个企业客户对应一行
class Tenant(BaseModel):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 套餐类型：trial(试用)/basic/pro/enterprise，决定配额与功能开关
    plan_type: Mapped[str] = mapped_column(String(20), nullable=False, default="trial")
    # 企业版升级先进入人工议价，当前套餐继续生效，避免议价期间中断服务（PRD §3.9）。
    pending_plan_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 每月对话配额，超出后可能需要升级套餐或限流（具体策略后续日程实现）
    quota_conversations_per_month: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 座席数量上限，控制该租户下能有多少个 TenantMember
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 租户状态：active(正常)/readonly(只读，欠费等场景)/closed(已关闭)
    # enterprise 套餐注册时为 pending_review（待审核，不自动激活）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # 配置向导当前步骤：basic→knowledge→mcp→team→done（PRD §3.1，有序推进不可跳步）
    wizard_step: Mapped[str] = mapped_column(String(20), nullable=False, default="basic")
    # 退款审批阈值：单笔退款金额超过此值需要人工审批（对应后续 human-in-the-loop 逻辑）
    # Numeric 列返回 Decimal，注解与列类型对齐（T-遗留1）
    refund_approval_threshold: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    # Widget 品牌定制（PRD §3.8）：嵌入组件主题色/Logo/欢迎语，租户自定义品牌
    widget_theme_color: Mapped[str] = mapped_column(String(20), nullable=False, default="#1890ff")
    widget_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    widget_welcome_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 规格 014 Widget 公共通道：API Key 仅存 SHA-256 哈希（防库泄露后直接使用）；域名白名单逗号分隔
    widget_api_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    widget_allowed_domains: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # 多模型计费折算（PRD §3.9）：租户默认模型，计费时按 MODEL_WEIGHTS 折算配额
    default_model: Mapped[str] = mapped_column(String(100), nullable=False, default="deepseek/deepseek-chat")
    # 迁移 d34： 演示租户持久标记 —— 所有权校验不依赖可碰撞的 name。
    # 归档旧演示实例时保留 True（识别历史演示租户），新实例重新置 True。
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
