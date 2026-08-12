"""
backend/app/services/mcp_service.py

职责: MCP 工具调用的中枢代理--权限矩阵校验、熔断检查、入参校验、双因子校验、
      JSON-RPC 2.0 信封封装、业务异常映射，是 Agent 与 MCP Server 之间的"守门人"。

核心设计:
  call_tool 是唯一的工具调用入口，接收 JSON-RPC 请求信封，返回响应信封。流程严格分层：
  解封信封 -> 注入 tenant_id -> 方法存在性校验 -> 权限校验 -> 熔断检查 -> 入参校验 ->
  分发 server handler -> 关键字段监控 -> 异常映射 -> 封装成功信封。
  关键设计是"方法存在性校验在权限校验之前"：未知方法返回 -32601（标准语义）而非 -32000 权限拒绝，
  避免泄露"方法存在但无权"的信息（攻击者能据此枚举系统有哪些工具）。

关键技术点:
  - 权限矩阵 ROLE_TOOL_MATRIX: 角色 -> 工具白名单，admin/agent/user 分级
  - 方法存在性优先于权限: 未知方法 -32601 在权限 -32000 之前，防信息泄露
  - 熔断三态集成: is_broken 短路 + 关键字段空值监控驱动 record_failure/success
  - 双因子防枚举: IdentityVerificationError 统一映射 -32602 "未查询到匹配信息"
  - 业务异常分组: 校验类 -> -32602，并发冲突类 -> -32603，惰性导入避循环依赖
  - tenant_id 注入: 服务端可信来源覆盖客户端传入值（宪法 II 防越权）

依赖关系:
  - 上游: app/agents/nodes/ticket_agent.py / order_agent.py（构造信封调 call_tool）
  - 下游: app/mcp_servers/base_server.py（信封+错误码）、ticket_server/order_server（分发）、
          app/core/circuit_breaker.py（熔断）、order_service/ticket_service（业务异常）
"""

import logging
import math
from decimal import Decimal, InvalidOperation

from app.core.circuit_breaker import is_broken, record_failure, record_success
from app.mcp_servers.base_server import (
    ERR_CIRCUIT_BROKEN,
    ERR_INTERNAL_ERROR,
    ERR_INVALID_PARAMS,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_PERMISSION_DENIED,
    IdentityVerificationError,
    build_error,
    build_success,
    parse_request,
)

logger = logging.getLogger(__name__)

# 角色 -> 允许调用的 MCP 工具白名单。权限矩阵是 RBAC 的核心：admin 全权，agent 可查可建不可退款，
# 终端用户 user 无权直接调工具（工具调用由 Agent 代发，role 用 agent）。
# 注: 退款审批（approve_refund）不在 MCP 工具集——审批是坐席后台操作（HTTP 层 + approver_id 审计），
# MCP 调用链无审批人身份（call_tool 只有 role/tenant_id），暴露给 LLM 会绕过"谁审批"的审计。
ROLE_TOOL_MATRIX: dict[str, set[str]] = {
    "admin": {"get_ticket_status", "create_ticket", "update_ticket", "query_order", "request_refund"},
    "agent": {"get_ticket_status", "create_ticket", "update_ticket", "query_order"},
    "user": set(),
}

# 工具方法 -> 所属 server 分发映射
_TICKET_METHODS = {"get_ticket_status", "create_ticket", "update_ticket"}
_ORDER_METHODS = {"query_order", "request_refund"}
_ALL_METHODS = _TICKET_METHODS | _ORDER_METHODS

# 关键字段监控（熔断用）：method -> 关键字段列表
# 工具返回结果中任一关键字段为空/缺失即视为"结构性故障"，计入 fail_count（US4）
_CRITICAL_FIELDS: dict[str, list[str]] = {
    "get_ticket_status": ["ticket_no", "status"],
    "query_order": ["order_no", "status"],
    "create_ticket": ["ticket_no"],
    "update_ticket": ["ticket_no", "status"],
    "request_refund": ["id"],
}

# 熔断兜底话术（T020）：工具熔断时返回的友好提示 + 转人工，遵循宪法 IV"不拒绝服务"
_FALLBACK_MESSAGES: dict[str, str] = {
    "get_ticket_status": "工单查询服务暂时不可用，已为您转接人工客服，请稍候。",
    "query_order": "订单查询服务暂时不可用，已为您转接人工客服，请稍候。",
}
_DEFAULT_FALLBACK = "该业务服务暂时不可用，已为您转接人工客服，请稍候。"


def _is_empty_result(method: str, result: object) -> bool:
    """判断结果关键字段是否为空/异常（熔断监控用）。

    流程:
      1. 非 dict 视为异常（结构性故障）
      2. 遍历该方法的关键字段，任一为 None 或空串即视为故障

    设计决策:
      只监控"关键字段"而非整个结果，是因为部分字段可选为空（如 description）属正常。
      ticket_no/status 这类核心字段空了说明数据出了问题（如 DB 查到脏数据），计入熔断
      能在后端持续异常时及时降级，避免用户反复收到残缺结果。
    """
    if not isinstance(result, dict):
        return True
    for field in _CRITICAL_FIELDS.get(method, []):
        val = result.get(field)
        if val is None or val == "":
            return True
    return False


def _fallback_message(method: str) -> str:
    """工具熔断时的兜底话术（提示转人工，宪法 IV 不拒绝服务）。"""
    return _FALLBACK_MESSAGES.get(method, _DEFAULT_FALLBACK)


class MCPError(Exception): pass
class PermissionDenied(MCPError): pass
class InvalidParams(MCPError): pass


def check_permission(role: str, tool_name: str) -> bool:
    """校验角色是否有权调用指定 MCP 工具。未知角色返回空集 -> 无权。"""
    allowed = ROLE_TOOL_MATRIX.get(role, set())
    return tool_name in allowed


def validate_params(tool_name: str, params: dict) -> None:
    """入参格式校验。不通过抛 InvalidParams（call_tool 映射为 -32602）。

    流程:
      1. 双因子查询工具必须提供 phone（FR-001）
      2. 工单号格式校验（TK- 前缀）
      3. 订单号格式校验（ORD- 前缀）
      4. 退款金额必须 > 0

    设计决策:
      格式校验在 server 层之前做"快失败"--格式错直接 -32602，不必进 server 查 DB。
      phone 在这里只校验"存在"，后 4 位匹配在 server 层（需查 DB 比对绑定号）。
    """
    # 双因子查询工具必须提供 phone（FR-001）
    if tool_name in ("get_ticket_status", "query_order"):
        if not params.get("phone"):
            raise InvalidParams("参数缺失: phone")
    if tool_name in ("get_ticket_status", "create_ticket", "update_ticket"):
        if "ticket_no" in params:
            tno = params["ticket_no"]
            if not tno or not tno.startswith("TK-"):
                raise InvalidParams(f"工单号格式错误: {tno}，应为 TK-XXXXXXXX")
    if tool_name in ("query_order", "request_refund"):
        if "order_no" in params:
            ono = params["order_no"]
            if not ono or not ono.startswith("ORD-"):
                raise InvalidParams(f"订单号格式错误: {ono}，应为 ORD-XXXXXXXX")
    if tool_name == "request_refund":
        # 016: 退款申请必须携带手机号做双因子校验（PRD 3.1.3）
        if not params.get("phone"):
            raise InvalidParams("参数缺失: phone")
        amount = params.get("amount", 0)
        # 类型防御（T14）: 金额必须是有限数值——str/NaN/Inf/bool 全部拒绝。
        # 原 `amount <= 0` 对 "abc" 抛 TypeError 误映射 -32603（应 -32602）；
        # NaN/Inf 与任何数比较恒 False 会穿透金额校验进业务；bool 是 int 子类会当 1 穿透。
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount):
            raise InvalidParams(f"退款金额必须是有限数值: {amount!r}")
        if amount <= 0:
            raise InvalidParams(f"退款金额必须大于 0: {amount}")
        # P1-16: 金额小数位不得超过 2 位（PRD 3.5 入参校验）
        try:
            decimal_amount = Decimal(str(amount))
            if decimal_amount.as_tuple().exponent < -2:
                raise InvalidParams("退款金额小数位不能超过 2 位")
        except InvalidOperation:
            raise InvalidParams(f"退款金额格式错误: {amount!r}")

    # P1-16: 字符串参数长度上限（对齐 DB 字段长度，防超长冲击数据库）
    for field, max_len in (("order_no", 30), ("ticket_no", 30), ("client_request_id", 100), ("reason", 500)):
        value = params.get(field)
        if value is not None and len(str(value)) > max_len:
            raise InvalidParams(f"参数 {field} 长度超过上限（{max_len}）")


def call_tool(
    role: str,
    envelope: dict,
    *,
    tenant_id: int | None = None,
    db=None,
) -> dict:
    """JSON-RPC 2.0 工具调用入口。返回响应信封（成功或错误）。

    流程（契约 contracts/mcp-protocol.md）:
      1. 解析请求信封（校验 jsonrpc/id/method）-> 非法 -32600
      2. 注入 tenant_id（覆盖客户端，宪法 II / FR-012）
      3. 方法存在性校验 -> 未知 -32601（优先于权限）
      4. 权限矩阵校验 -> -32000
      5. 熔断检查 -> -32001 + 兜底话术
      6. 入参校验 -> -32602
      7. 分发 server handler（异常映射）
      8. 关键字段空值监控 -> 失败 fail_count++ / 成功 record_success
      9. 封装成功信封

    设计决策:
      方法存在性校验（步骤 3）在权限校验（步骤 4）之前，是防信息泄露的关键--若先校验权限，
      攻击者调一个不存在的工具会得到 -32000"无权"，调一个存在但无权的工具也是 -32000，
      两者响应相同就无法区分；而先校验存在性，不存在的方法返回 -32601、存在但无权返回 -32000，
      攻击者反而能枚举出系统有哪些工具。所以这里的顺序看似反直觉（权限通常先做），
      实则遵循 JSON-RPC 2.0 标准语义：方法不存在是协议层错误，应优先于业务层权限。
      注：实际部署中 tools/list 已公开工具清单，此处顺序更多是协议合规性考量。

    Args:
        role: 调用方角色（权限校验）
        envelope: JSON-RPC 请求信封 {"jsonrpc":"2.0","id":...,"method":...,"params":...}
        tenant_id: 服务端可信 tenant_id（JWT/session），覆盖 params 中客户端传入值
        db: 可选 Session 注入（测试复用；不传则 server 内部新建）
    Returns:
        JSON-RPC 响应信封 dict
    """
    # 1. 解析请求信封（结构校验）
    try:
        req_id, method, params = parse_request(envelope)
    except ValueError:
        # 信封结构非法：尽量取 id（取不到回退 null）返回 -32600
        req_id = envelope.get("id") if isinstance(envelope, dict) else None
        return build_error(ERR_INVALID_REQUEST, req_id)

    # 2. 注入 tenant_id（覆盖客户端传入值，宪法原则 II / FR-012 / SC-006）
    # ⚠️ 关键：tenant_id 来自服务端可信来源（JWT/session），绝不信客户端，防越权访问他租户数据
    # fail-closed（T11）: tenant_id 缺失 = 调用上下文不可信，直接拒绝；
    # 原条件注入在 None 时会把 params 里客户端自报值原样透传（配合 server 层默认租户 1 可越权写他租户）
    if tenant_id is None:
        return build_error(ERR_PERMISSION_DENIED, req_id, "调用上下文缺少租户信息")
    params["tenant_id"] = tenant_id

    # 3. 方法存在性校验（未知方法 -> -32601，优先于权限，符合 JSON-RPC 语义）
    # 先校验方法存在性再校验权限：未知方法返回 -32601（标准 JSON-RPC 语义），
    # 而非 -32000 权限拒绝（避免泄露"方法存在但无权"的信息）
    if method not in _ALL_METHODS:
        return build_error(ERR_METHOD_NOT_FOUND, req_id, f"Method not found: {method}")

    # 4. 权限校验 -> -32000（角色无权调用该工具）
    if not check_permission(role, method):
        return build_error(
            ERR_PERMISSION_DENIED, req_id, f"角色 {role} 无权调用工具 {method}"
        )

    # 5. 熔断检查（US4）：工具处于熔断中 -> -32001 + 兜底话术（不调 handler，直接降级）
    if is_broken(method):
        return build_error(ERR_CIRCUIT_BROKEN, req_id, _fallback_message(method))

    # 6. 入参校验 -> -32602
    try:
        validate_params(method, params)
    except InvalidParams as e:
        return build_error(ERR_INVALID_PARAMS, req_id, str(e))

    # 7. 分发到 server handler
    try:
        if method in _TICKET_METHODS:
            from app.mcp_servers.ticket_server import handle_ticket_tool
            data = handle_ticket_tool(method, params, db=db)
        elif method in _ORDER_METHODS:
            from app.mcp_servers.order_server import handle_order_tool
            data = handle_order_tool(method, params, db=db)
        else:
            return build_error(ERR_METHOD_NOT_FOUND, req_id, f"Method not found: {method}")
    except IdentityVerificationError:
        # 双因子失败/记录不存在 -> 统一 -32602 "未查询到匹配信息"（防枚举，SC-002）
        return build_error(ERR_INVALID_PARAMS, req_id, "未查询到匹配信息")
    except InvalidParams as e:
        return build_error(ERR_INVALID_PARAMS, req_id, str(e))
    except _BusinessValidationErrors as e:
        # 业务校验失败（退款超额/非法状态跃迁/记录不存在等）-> -32602
        return build_error(ERR_INVALID_PARAMS, req_id, str(e))
    except _ConcurrencyConflict as e:
        # 乐观锁冲突 -> -32603（可重试）
        return build_error(ERR_INTERNAL_ERROR, req_id, str(e))
    except Exception as e:
        # 兜底：未预期的异常 -> -32603，记全栈日志便于排查
        logger.exception(f"MCP tool {method} internal error")
        return build_error(ERR_INTERNAL_ERROR, req_id, "Internal error")

    # 8. 关键字段空值监控（US4）：空/异常 -> record_failure（达阈值熔断）；正常 -> record_success（half_open 解除）
    if _is_empty_result(method, data):
        record_failure(method)
    else:
        record_success(method)

    # 9. 封装成功信封
    return build_success(data, req_id)


# ─── 业务异常分组（延迟导入避免循环，按类型聚合映射）───
def _import_business_errors():
    """惰性导入业务异常类，避免模块加载期循环依赖。

    设计决策:
      order_service / ticket_service 可能反向依赖 mcp_service 的常量，模块加载期直接 import
      会循环。放到函数内惰性导入，在模块加载完成后才执行，打破循环。
      按异常语义分两组：校验类（业务规则违反，-> -32602）和并发冲突类（乐观锁，-> -32603 可重试）。
    """
    from app.services.order_service import (
        DuplicateRefund,
        InvalidTransition,
        OrderError,
        RefundExceedsPaid,
        VersionConflict,
    )
    from app.services.ticket_service import TicketError, TicketNotFound
    # 业务校验类（-> -32602）
    validation = (RefundExceedsPaid, InvalidTransition, DuplicateRefund,
                  TicketNotFound, TicketError, OrderError)
    # 并发冲突类（-> -32603）
    concurrency = (VersionConflict,)
    return validation, concurrency


# 模块加载时构建异常元组，供 except 匹配
# 若 service 模块缺失导致导入失败，降级为空元组（except () 永不匹配，异常落到兜底 -32603）
try:
    _BusinessValidationErrors, _ConcurrencyConflict = _import_business_errors()
except Exception:  # noqa: BLE001  启动期某 service 模块缺失时不阻断 call_tool 基本流程
    _BusinessValidationErrors = ()
    _ConcurrencyConflict = ()
