"""
backend/app/api/v1/mcp/server.py

职责: MCP HTTP 传输层 -- 将 HTTP 请求转换为 JSON-RPC 2.0 信封，交由 mcp_service 分发

  HTTP -> JSON-RPC 转换:
    /tools/list  -> 直接返回 JSON-RPC 信封 {jsonrpc, result: {tools: [...]}}
    /tools/call  -> 请求体 {name, arguments} -> build_request() 构造 JSON-RPC 信封
                   -> mcp_service.call_tool(role, envelope, tenant_id, db)
                   -> 返回 JSON-RPC 响应信封（成功含 result，失败含 error.code）

  租户隔离红线（宪法原则 II）:
    tenant_id 只从 JWT（request.state.tenant_id）取，绝不信任客户端传入。
    mcp_service 内部将 tenant_id 注入 params，覆盖客户端自报值（FR-012 / SC-006）。

  本文件只做传输层协议封装，不实现工具业务逻辑（业务逻辑在 app/mcp_servers/）。

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/mcp
  - 下游: app.mcp_servers.base_server（build_request）、app.services.mcp_service（call_tool）
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.api.deps import get_db
from app.mcp_servers.base_server import JSONRPC_VERSION, build_request
from app.mcp_servers.order_server import ORDER_TOOLS
from app.mcp_servers.ticket_server import TICKET_TOOLS

router = APIRouter()

ALL_MCP_TOOLS = TICKET_TOOLS + ORDER_TOOLS


class ToolCallRequest(BaseModel):
    """MCP tools/call 请求体 Schema（替换裸 dict，强制类型校验）。"""
    name: str = Field(..., min_length=1, max_length=100, description="工具名称")
    arguments: dict = Field(default_factory=dict, description="工具参数")


@router.get("/tools/list")
def list_tools(request: Request):
    """MCP tools/list: 返回所有已注册工具的 JSON-RPC 信封。"""
    return {"jsonrpc": JSONRPC_VERSION, "result": {"tools": ALL_MCP_TOOLS}}


@router.post("/tools/call")
def call_tool(request: Request, body: ToolCallRequest, db: DbSession = Depends(get_db)):
    """MCP tools/call: 将 {name, arguments} 构造为 JSON-RPC 信封，交 mcp_service 分发。

    请求体: {"name": "<tool>", "arguments": {...}}
    响应体: JSON-RPC 信封 {"jsonrpc":"2.0","id":"...","result":{...}|"error":{...}}
    """
    from app.services.mcp_service import call_tool as mcp_call

    tool_name = body.name
    arguments = body.arguments
    role = getattr(request.state, "role", "user")

    # 租户隔离红线: tenant_id 必须来自 JWT（request.state），绝不信任客户端传入
    # call_tool 内部将 tenant_id 注入 params，覆盖客户端自报值（FR-012 / SC-006）
    tenant_id = getattr(request.state, "tenant_id", None)

    # 构造 JSON-RPC 2.0 请求信封，交 mcp_service 处理（解析/权限/校验/分发/封装）
    envelope = build_request(tool_name, arguments)
    return mcp_call(role, envelope, tenant_id=tenant_id, db=db)
