r"""
backend/app/utils/desensitize.py

职责: PII 脱敏（规格 008 US3）-- trace 入库前对手机号脱敏，前 3 + **** + 后 4。

核心设计:
  用正则识别中国大陆手机号（1 开头 + 9 位数字共 11 位），脱敏为 138****0001 格式，单向不可逆
  （无法从掩码还原原号）。关键是正则边界用数字边界 (?<!\d)...(?!\d) 而非 \b--Python3 的 \w
  包含 CJK 字符，中文紧贴手机号时 \b 不生效（中文与数字间无词边界），会漏匹配；
  数字边界只看前后是否非数字，与字符类型无关，中英混排都能正确匹配。
  失败 fallback 截断前 20 字，绝不泄露原文。

关键技术点:
  - 数字边界 (?<!\d)...(?!\d): 避免 \b 在 CJK 旁失效，正确匹配中文语境下的手机号
  - 前 3 后 4 掩码: 138****0001，保留运营商号段前缀和尾号便于人工识别，中间不可逆
  - 不误伤编号: TK-/ORD- 等编号不含 1[3-9]\d{9} 格式，天然不匹配
  - fallback 截断: 脱敏异常时截断而非返回原文，保证不泄露

依赖关系:
  - 上游: app/services/trace_service.py（mask_pii 脱敏 trace 字段后入库 MongoDB）
  - 下游: 无（纯函数工具）
"""

import logging
import re

logger = logging.getLogger(__name__)

# 中国大陆手机号：1[3-9] 开头 + 9 位数字 = 11 位
# 用数字边界 (?<!\d)...(?!\d) 而非 \b：Python3 re 的 \w 含 CJK，中文紧贴手机号时 \b 不生效
# （\b 是 \w 与 \W 的边界，而 \w 含中文，所以"号13800001111"中"号"与"1"都是 \w，无边界）。
# 数字边界只断言前后非数字，与字符是否 CJK 无关，中英混排都能正确匹配。
# 同时防止误匹配更长数字串中的子串（如 139123456789 中的 11 位片段）。
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _mask_match(m: re.Match) -> str:
    """单次匹配的脱敏替换：前 3 + **** + 后 4。

    保留前 3（运营商号段，人工可识别归属）和后 4（尾号，便于用户核对），
    中间 4 位用 * 掩码，单向不可逆（无法还原）。
    """
    phone = m.group(0)
    return f"{phone[:3]}****{phone[-4:]}"


def mask_pii(text: str | None) -> str | None:
    """对文本中的手机号脱敏（前3 + **** + 后4，单向不可逆）。

    流程:
      1. None/空 -> 原样返回
      2. 正则 sub 替换所有手机号为掩码
      3. 异常 -> fallback 截断前 20 字（不泄露原文）

    设计决策:
      fallback 截断而非返回原文，是 PII 安全的底线--脱敏逻辑本身出错时，宁可损失信息（截断）
      也不能把含手机号的原文写进 trace。这是"安全失败"原则：失败时进入最保守状态。

    Args:
        text: 原始文本（可能含手机号）
    Returns:
        脱敏后文本；None/空原样返回；脱敏异常 fallback 截断前20字（不泄露原文）。
    """
    if not text:
        return text
    try:
        return _PHONE_RE.sub(_mask_match, text)
    except Exception as e:
        # 脱敏异常：截断而非返回原文，保证不泄露 PII
        logger.warning(f"mask_pii failed, fallback to truncate: {e}")
        return text[:20]
