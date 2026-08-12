"""
backend/app/mcp_servers/ticket_server.py

职责: 工单域 MCP Server--暴露 get_ticket_status/create_ticket/update_ticket 三个工具，
      按 JSON-RPC 2.0 协议定义 inputSchema，分发执行工单业务逻辑，强制 tenant_id 过滤。

核心设计:
  本文件是"工具实现层"--定义工具有哪些（TICKET_TOOLS 的 inputSchema 即 tools/list 返回内容）、
  以及每个工具怎么执行（handle_ticket_tool 分发到具体业务函数）。它不处理 JSON-RPC 信封
  （那是 mcp_service 的职责），只接收已解析的 (tool_name, params) 返回业务 dict 或抛异常。
  006 安全加固后，get_ticket_status 强制双因子：phone 后 4 位必须匹配工单绑定手机号，
  不存在/不匹配统一抛 IdentityVerificationError 防枚举。

关键技术点:
  - tools/list inputSchema: JSON Schema 描述工具入参，LLM 据此构造调用参数
  - db 注入: 可选传 Session，不传内部新建；测试复用同一 Session 保证事务可见性
  - 双因子校验: verify_phone_suffix 比对后 4 位，统一异常防枚举
  - 返回值脱敏: 工单详情不含 phone 字段，避免把绑定手机号回显给用户

依赖关系:
  - 上游: app/services/mcp_service.py（call_tool 分发到 handle_ticket_tool）
  - 下游: app/mcp_servers/base_server.py（verify_phone_suffix/IdentityVerificationError）、
          app/services/ticket_service.py（create_ticket/update_ticket_status）、app/crud/ticket.py
"""

import logging

from sqlalchemy.orm import Session

from app.mcp_servers.base_server import IdentityVerificationError, verify_phone_suffix

logger = logging.getLogger(__name__)

# MCP Tool 定义（标准 tools/list 返回格式）。每个工具的 inputSchema 是 JSON Schema，
# LLM 在 FC 模式下据此构造调用参数，MCP 模式下 mcp_service 据此做入参校验。
# tenant_id 标注为"服务端注入"——客户端传入会被 mcp_service 覆盖（宪法原则 II 防越权）。
TICKET_TOOLS = [
    {
        "name": "get_ticket_status",
        "description": "查询工单状态和详情，需要提供工单编号 + 绑定手机号（双因子校验）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_no": {"type": "string", "description": "工单编号，格式 TK-XXXXXXXX"},
                "phone": {"type": "string", "description": "终端用户手机号（校验后4位匹配工单绑定手机号）"},
                "tenant_id": {"type": "integer", "description": "租户ID（服务端注入，客户端传入被覆盖）"},
            },
            "required": ["ticket_no", "phone"],
        },
    },
    {
        "name": "create_ticket",
        "description": "创建新工单",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
            },
            "required": ["title"],  # T29: tenant_id 服务端注入，不要求客户端传
        },
    },
    {
        "name": "update_ticket",
        "description": "更新工单状态",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_no": {"type": "string"},
                "tenant_id": {"type": "integer"},
                "new_status": {"type": "string", "enum": ["open", "in_progress", "resolved", "closed"]},
            },
            "required": ["ticket_no", "new_status"],  # T29: tenant_id 服务端注入
        },
    },
]


def handle_ticket_tool(tool_name: str, params: dict, *, db: Session | None = None) -> dict:
    """MCP tools/call 分发器：根据 tool_name 调用对应的工单业务逻辑。

    流程:
      1. 决定 Session：外部注入则复用，不传则内部新建（own_session 标记负责关闭）
      2. 取 tenant_id（由 mcp_service 注入，缺失拒绝——T11 fail-closed）
      3. 按 tool_name 分发到具体业务函数 / ticket_service
      4. 写操作（create/update）成功后 commit
      5. own_session 在 finally 关闭，保证异常时也释放连接

    设计决策:
      db 注入模式是为了测试复用--测试用同一 in-memory SQLite Session 注入，才能在断言时
      读到未提交的事务数据。生产路径不传 db，内部新建独立 Session，请求间隔离。
      import 放函数内（如 ticket_service）是规避循环导入：service 层可能反向依赖 server 层的常量。

    Args:
        tool_name: 工具方法名
        params: JSON-RPC params（已注入 tenant_id）
        db: 可选 Session（测试/调用方注入）；不传则内部新建并在结束时关闭
    Returns:
        业务结果 dict（由 mcp_service 封装为 JSON-RPC 成功信封）
    Raises:
        IdentityVerificationError: 双因子校验失败/工单不存在（统一消息防枚举）
        其他业务异常上抛由 mcp_service 映射为对应错误码
    """
    own_session = db is None
    if own_session:
        from app.core.database import SessionLocal
        db = SessionLocal()
    try:
        # fail-closed（T11）: tenant_id 由 mcp_service.call_tool 注入，
        # 缺失说明绕过 call_tool 直调 handler，拒绝（正常链路不可能触发）
        tenant_id = params.get("tenant_id")
        if tenant_id is None:
            raise ValueError("missing tenant_id")

        if tool_name == "get_ticket_status":
            # 查询走双因子校验，不需 commit
            return _get_ticket_status(db, tenant_id, params)

        elif tool_name == "create_ticket":
            from app.services.ticket_service import create_ticket
            result = create_ticket(
                db, tenant_id=tenant_id, title=params["title"],
                description=params.get("description"),
                priority=params.get("priority", "medium"),
            )
            db.commit()
            return result

        elif tool_name == "update_ticket":
            from app.services.ticket_service import update_ticket_status
            result = update_ticket_status(
                db, ticket_no=params["ticket_no"],
                tenant_id=tenant_id, new_status=params["new_status"],
            )
            db.commit()
            return result

        else:
            raise ValueError(f"未知工单工具: {tool_name}")
    finally:
        # own_session 才关闭，避免误关外部注入的 Session
        if own_session:
            db.close()


def _get_ticket_status(db: Session, tenant_id: int, params: dict) -> dict:
    """查询工单状态（含双因子身份校验）。

    流程（契约 security-api.md）:
      1. 按 (ticket_no, tenant_id) 查工单
      2. 不存在 -> IdentityVerificationError（统一"未查询到匹配信息"，防枚举）
      3. 校验 phone 后4位 == ticket.phone 后4位
      4. 不匹配 / 未绑定 -> 同上统一异常
      5. 匹配 -> 返回工单详情（不含 phone，避免泄露绑定号）

    设计决策:
      "不存在"和"手机号不匹配"返回完全相同的异常和消息，是防枚举的核心——攻击者无法通过
      不同的错误响应判断某个工单号是否存在。这是把"数据存在性"和"身份匹配性"两个失败原因
      合并成一个模糊错误的标准做法。返回值刻意不含 phone 字段，避免把绑定手机号回显泄露。

    Args:
        db: 数据库 Session
        tenant_id: 租户 ID（多租户隔离）
        params: 含 ticket_no 和 phone
    Returns:
        工单详情 dict（不含 phone）
    Raises:
        IdentityVerificationError: 工单不存在或双因子校验失败
    """
    from app.crud.ticket import get_by_ticket_no

    ticket_no = params["ticket_no"]
    phone = params.get("phone")

    # 按 (ticket_no, tenant_id) 查询，tenant_id 过滤保证租户隔离
    ticket = get_by_ticket_no(db, tenant_id, ticket_no)
    if ticket is None:
        # 工单不存在：统一异常，不暴露"不存在"这一事实
        raise IdentityVerificationError()
    # 双因子：手机号后 4 位必须匹配工单绑定手机号
    if not verify_phone_suffix(phone, ticket.phone):
        # 不匹配/未绑定：同样的异常，与"不存在"不可区分
        raise IdentityVerificationError()

    # 返回详情，刻意排除 phone 字段，避免回显绑定手机号
    return {
        "ticket_id": ticket.id, "ticket_no": ticket.ticket_no, "title": ticket.title,
        "status": ticket.status, "priority": ticket.priority, "description": ticket.description,
    }
