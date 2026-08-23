"""
backend/app/services/security_service.py

职责: Prompt 注入检测（规则库 + 关键词正则）+ 安全事件记录（security_incidents 表）。

核心设计:
  轻量规则库检测，5 条规则覆盖典型注入手法（忽略指令/角色劫持/系统提示词泄露/越狱/角色扮演）。
  high 级别转人工（不拒绝服务，宪法 IV 兜底），medium 级别放行但标记观察。
  不用 ML/LLM 二次判断，原因是：①注入检测必须在主对话前同步完成，加 LLM 二次判断会引入
  额外延迟和成本；②LLM 判断本身可能被注入绕过（用 LLM 防 LLM 注入是悖论）；③规则库可解释、
  可审计、零误判漂移，demo 规模下足够；④命中记录 SecurityIncident 供事后审查，可据此优化规则。

关键技术点:
  - 规则优先级: high 规则在前，first-match 即返回，确保 high 命中优先于 medium
  - 双语正则: 中英文关键词都覆盖，re.IGNORECASE 大小写不敏感
  - 分级处置: high -> transfer_to_human（转人工），medium -> allow（标记观察）
  - 事件留痕: record_incident 写 security_incidents，preview 截断 500 字防爆库

依赖关系:
  - 上游: app/services/agent_service.py（_detect_injection 调 detect_prompt_injection，high 则短路转人工）
  - 下游: app/models/security_incident.py（SecurityIncident 表）
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session as DbSession

logger = logging.getLogger(__name__)


@dataclass
class InjectionResult:
    """注入检测结果。

    字段说明:
      - is_risky: 是否有风险
      - risk_level: low/medium/high
      - rule_hit: 命中的规则名（无命中为 None）
      - action: 处置动作 allow（放行）/ transfer_to_human（转人工）
    """

    is_risky: bool
    risk_level: str  # low / medium / high
    rule_hit: str | None  # 命中规则名
    action: str  # "allow" / "transfer_to_human"


# 规则库：(rule_name, pattern, risk_level)
# 顺序即匹配优先级：high 规则在前，medium 在后，first-match 即返回，确保 high 命中优先于 medium。
# 中英文双语覆盖，re.IGNORECASE 大小写不敏感。
# 5 条规则对应 5 类典型注入手法：
# - ignore_instruction: "忽略上述指令"类，企图覆盖系统提示词
# - role_hijack: "你现在是 XX"类，企图劫持 Agent 角色
# - system_prompt_leak: 企图套取系统提示词/规则
# - jailbreak_attempt: 越狱/DAN 等知名越狱手法
# - role_play: 角色扮演类，medium 级别（可能是正常请求如"扮演客服"，故只标记）
_INJECTION_RULES: list[tuple[str, str, str]] = [
    ("ignore_instruction", r"忽略.*指令|ignore.*previous.*instruction", "high"),
    ("role_hijack", r"你现在是|you are now", "high"),
    ("system_prompt_leak", r"系统提示词|system prompt|输出.*规则|reveal.*rules", "high"),
    ("jailbreak_attempt", r"越狱|jailbreak|\bDAN\b", "high"),
    ("role_play", r"扮演.*角色|pretend.*to be", "medium"),
]

# 预编译正则（IGNORECASE；不用 DOTALL，注入通常单行，跨行可后续扩展）
# 预编译是为了性能：每次检测都跑 5 条正则，预编译省去重复编译开销
_COMPILED_RULES = [
    (name, re.compile(pattern, re.IGNORECASE), level)
    for name, pattern, level in _INJECTION_RULES
]


def detect_prompt_injection(text: str | None) -> InjectionResult:
    """检测用户输入是否含 Prompt 注入风险。

    流程:
      1. 空输入 -> 低风险放行
      2. 按规则顺序（high 优先）逐条正则匹配
      3. 首条命中即返回：high -> transfer_to_human，medium -> allow
      4. 全未命中 -> 低风险放行

    设计决策:
      first-match 即返回而非累计所有命中，是因为 high 规则命中后处置已确定（转人工），
      无需继续匹配 medium。规则顺序保证 high 在前，避免被 medium 先匹配到而降级处置。
      空输入返回低风险而非拦截，避免空消息误判。

    Args:
        text: 用户原始消息
    Returns:
        InjectionResult：命中则 is_risky=True + risk_level + rule_hit + action；
        high -> action=transfer_to_human；medium -> action=allow（标记）。
    """
    if not text:
        return InjectionResult(is_risky=False, risk_level="low", rule_hit=None, action="allow")

    for rule_name, regex, level in _COMPILED_RULES:
        if regex.search(text):
            # high 转人工（不拒绝服务，宪法 IV），medium 放行标记
            action = "transfer_to_human" if level == "high" else "allow"
            logger.info(
                f"Injection detected: rule={rule_name}, level={level}, action={action}"
            )
            return InjectionResult(
                is_risky=True, risk_level=level, rule_hit=rule_name, action=action
            )
    return InjectionResult(is_risky=False, risk_level="low", rule_hit=None, action="allow")


def record_incident(
    db: DbSession,
    *,
    tenant_id: int,
    session_id: int | None,
    user_message: str,
    rule_hit: str,
    risk_level: str,
    action_taken: str,
) -> None:
    """记录一次安全事件到 security_incidents 表（FR-008）。

    流程:
      1. 用户消息截断到 500 字（preview，防爆库 + 脱敏考量）
      2. 构造 SecurityIncident 写入并 commit

    设计决策:
      只存 preview 而非全文，一是防恶意超长输入撑爆表，二是减少 PII 留存（手机号可能在消息里）。
      session_id 可为 None（注入发生在会话建立前的情况）。tenant_id 强制带，保证多租户隔离可审计。
      事件留痕供事后审查与规则优化--误判样本可据此调整规则，漏判样本可新增规则。

    Args:
        tenant_id: 租户归属（宪法 II）
        session_id: 关联会话（可为 None）
        user_message: 触发规则的用户输入（脱敏截断到 500 字）
        rule_hit: 命中的规则名
        risk_level: low / medium / high
        action_taken: blocked / transferred_to_human
    """
    from app.models.security_incident import SecurityIncident

    # 截断到 500 字作为 preview，防爆库 + 减少 PII 留存
    from app.utils.desensitize import mask_pii
    preview = mask_pii((user_message or "")[:500])
    incident = SecurityIncident(
        tenant_id=tenant_id,
        session_id=session_id,
        user_message_preview=preview,
        rule_hit=rule_hit,
        risk_level=risk_level,
        action_taken=action_taken,
    )
    db.add(incident)
    db.commit()
    logger.info(
        f"Security incident recorded: tenant={tenant_id}, session={session_id}, "
        f"rule={rule_hit}, level={risk_level}"
    )
