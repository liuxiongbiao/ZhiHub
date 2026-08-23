"""
backend/app/models/knowledge_document.py

职责: knowledge_documents 表 ORM 模型定义（知识库文档元数据）

核心设计:
  知识库文档元数据，status 驱动异步处理流水线，version/is_latest 解决矛盾片段。
  关键字段: tenant_id(隔离键)、filename、status(pending/processing/ready/failed)、version、is_latest、
            chunk_count、expires_at(规格 010 新增，到期摘除)、quality_status(规格 010 新增，质量状态)
  简化: 保留 version/is_latest（矛盾片段必须处理），砍掉精细质检与生命周期治理的完整实现。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class KnowledgeDocument(BaseModel):
    __tablename__ = "knowledge_documents"

    # 租户隔离键（所有查询必须带此条件）
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # 原始文件名（同名文件通过 version 区分版本）
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    # 文件格式：pdf / docx / md
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 文件大小（bytes）
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # 处理状态：pending → processing → ready / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # 失败原因（status=failed 时填充）
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 版本号：同名文件每次上传 +1；首次上传默认 1
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 是否最新版本：旧版本归档后设为 False，不进检索库
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 切片总数（异步处理完成后填充）
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 规格 010 新增：文档有效期到期时间（默认上传+90天，到期摘除检索库）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 规格 010 新增：质量状态（ready 正常 / pending_review 乱码待人工复核 / expired 已过期摘除）
    quality_status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
