"""
backend/app/mcp_servers/base_server.py

职责: MCP 协议的最底层——JSON-RPC 2.0 标准信封的构造与解析，以及双因子身份校验工具。

核心设计:
  本文件不依赖任何官方 MCP SDK，纯手写 JSON-RPC 2.0 信封。原因是官方 SDK 默认走 stdio 传输，
  而本系统是 Web 服务架构，工具调用走 HTTP（见 app/api/v1/mcp/server.py），手写信封既剥离了
  信封三件套：请求 / 成功 / 错误，外加一个 parse_request 做入站信封校验。
  错误码前 5 个（-32x00 系列）严格对齐 JSON-RPC 2.0 规范，后 2 个（-32000/-32001）是业务扩展。

关键技术点:
  - JSON-RPC 2.0 信封: 请求带 method+params，成功带 result，错误带 error{code,message}
  - 错误码分层: -32700~-32603 是协议层标准码，-32000~-32099 留给业务自定义（规范允许的保留段）
  - 双因子校验 verify_phone_suffix: 比对手机号后 4 位，防枚举统一报错
  - IdentityVerificationError: 业务层抛出后由 mcp_service 统一映射为 -32602，消息恒定以防枚举

依赖关系:
  - 上游: app/services/mcp_service.py（调用 build_request 构造请求、parse_request 解析入站）、
          app/mcp_servers/ticket_server.py / order_server.py（调用 verify_phone_suffix 做双因子、抛 IdentityVerificationError）
  - 下游: 无（纯协议工具，零业务依赖）
"""

import uuid
from typing import Any

from app.utils.validators import verify_phone_suffix  # noqa: F401  # 016: 逻辑收敛到 validators，此处再导出保持兼容

JSONRPC_VERSION = "2.0"

# ─── 错误码（对齐 JSON-RPC 2.0 标准 + 业务扩展 -32xxx）───
# -32700~-32603 是 JSON-RPC 2.0 规范定义的标准错误码，语义固定不可改；
# -32000~-32099 是规范留给服务端自定义的保留段，本项目在此扩展"权限拒绝"和"工具熔断"两个业务错误。
ERR_PARSE_ERROR = -32700          # 解析失败：信封不是合法 JSON
ERR_INVALID_REQUEST = -32600      # 非法请求：信封结构不符合 JSON-RPC 2.0（缺字段/类型错）
ERR_METHOD_NOT_FOUND = -32601     # 方法不存在：调用了未注册的工具
ERR_INVALID_PARAMS = -32602       # 入参非法：参数缺失/类型错/双因子校验失败（业务复用此码）
ERR_INTERNAL_ERROR = -32603       # 内部错误：服务端执行异常
ERR_PERMISSION_DENIED = -32000    # 业务扩展：权限矩阵拦截（租户无权调用该工具）
ERR_CIRCUIT_BROKEN = -32001       # 业务扩展：工具熔断中（连续失败触发熔断，详见 circuit_breaker.py）

# 错误码 -> 默认消息映射。build_error 未传 message 时回退到此处，保证每个码都有标准文案。
ERROR_MESSAGES = {
    ERR_PARSE_ERROR: "Parse error",
    ERR_INVALID_REQUEST: "Invalid Request",
    ERR_METHOD_NOT_FOUND: "Method not found",
    ERR_INVALID_PARAMS: "Invalid params",
    ERR_INTERNAL_ERROR: "Internal error",
    ERR_PERMISSION_DENIED: "Permission denied",
    ERR_CIRCUIT_BROKEN: "Tool circuit broken",
}


class IdentityVerificationError(Exception):
    """双因子身份校验失败（手机号不匹配/记录不存在/未绑定手机号）。

    流程:
      1. MCP server 层（ticket_server/order_server）调用 verify_phone_suffix 校验后 4 位
      2. 校验失败或记录不存在时抛出本异常
      3. mcp_service.call_tool 捕获后统一映射为 -32602 "未查询到匹配信息"

    设计决策:
      统一消息以防枚举攻击：不区分"记录不存在"与"身份不匹配"，避免攻击者通过不同错误消息
      探测出哪些手机号/工单号在系统中真实存在。这是 OWASP 防枚举的标准做法。
    """

    def __init__(self, message: str = "未查询到匹配信息"):
        # 默认消息恒定，调用方一般不覆盖，确保对外的错误消息无法被用来区分失败原因
        super().__init__(message)


def build_request(method: str, params: dict | None = None, req_id: str | None = None) -> dict:
    """构造 JSON-RPC 2.0 请求信封。

    Args:
        method: 要调用的工具名（如 "get_ticket_status"）
        params: 工具入参字典（如 {"ticket_no": "T001", "phone": "138..."}）
        req_id: 请求 id；不传则生成 uuid4。id 用于客户端匹配请求与响应。
    Returns:
        {"jsonrpc":"2.0","id":"<uuid>","method":"<tool>","params":{...}}
    """
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id or str(uuid.uuid4()),
        "method": method,
        "params": params or {},
    }


def build_success(result: Any, req_id: str) -> dict:
    """构造成功响应信封。

    Args:
        result: 工具执行结果（任意可序列化对象）
        req_id: 原样回传请求 id，客户端据此匹配
    Returns:
        {"jsonrpc":"2.0","id":"<uuid>","result":{...}}
    """
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


def build_error(
    code: int, req_id: str, message: str | None = None, data: Any = None
) -> dict:
    """构造错误响应信封。

    流程:
      1. 取消息：优先用传入的 message，否则回退到 ERROR_MESSAGES 默认文案
      2. 可选附 data 字段（如校验失败的字段详情），不传则不带

    设计决策:
      错误信封里 id 原样回传，即使解析失败也尽量回传（parse_request 失败时 id 可能为 None，
      由调用方决定）。error 对象只有 code+message 是必填，data 可选——这是 JSON-RPC 2.0 规范要求。

    Args:
        code: 错误码（见 ERR_* 常量）
        req_id: 请求 id（原样回传，便于客户端匹配）
        message: 错误消息（不传则用错误码默认消息）
        data: 可选额外数据（如校验错误的字段详情）
    """
    err: dict[str, Any] = {
        "code": code,
        "message": message or ERROR_MESSAGES.get(code, "Unknown error"),
    }
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": err}


def parse_request(envelope: dict) -> tuple[str, str, dict]:
    """解析入站请求信封，返回 (req_id, method, params)。

    流程:
      1. 必须是 dict，否则非法
      2. jsonrpc 字段必须等于 "2.0"
      3. id 不能为 None（请求必须有 id，通知才允许无 id，但 MCP 不用通知）
      4. method 必须是非空字符串
      5. params 缺省为 {}，且必须是 dict

    设计决策:
      非法信封抛 ValueError 而非返回错误码——因为信封本身非法时连 id 都取不到，
      无法构造合规的错误响应。调用方（mcp_service）捕获 ValueError 后，用空 id 或
      能取到的 id 构造 ERR_INVALID_REQUEST 响应。这是协议层"先校验后处理"的标准做法。

    Args:
        envelope: 原始入站信封字典
    Returns:
        (req_id, method, params) 三元组
    Raises:
        ValueError: 信封不符合 JSON-RPC 2.0 结构
    """
    if not isinstance(envelope, dict):
        raise ValueError("Invalid Request: envelope must be an object")
    if envelope.get("jsonrpc") != JSONRPC_VERSION:
        raise ValueError("Invalid Request: missing or wrong jsonrpc version")
    req_id = envelope.get("id")
    if req_id is None:
        raise ValueError("Invalid Request: missing id")
    method = envelope.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError("Invalid Request: missing or invalid method")
    params = envelope.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("Invalid Request: params must be an object")
    return req_id, method, params
