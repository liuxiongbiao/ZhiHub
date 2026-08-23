"""
backend/scripts/ci_audit_gate.py

职责: CI 依赖安全审计门禁——解析 pip-audit 的 JSON 输出，
      仅当存在 HIGH / CRITICAL（或无法判定严重度 UNKNOWN）漏洞时以退出码 1 阻断 CI，
      LOW / MEDIUM 仅告警不阻断（避免因上游暂无修复版本的次要漏洞永久卡死流水线）。

背景: pip-audit 2.7+ 移除了 --severity 过滤选项且 CLI 持续变动，
      本脚本以 JSON 为契约，对 pip-audit 版本变化免疫。

用法:
  pip-audit -r requirements.txt -f json -o audit.json
  python scripts/ci_audit_gate.py audit.json [--ignore-file scripts/audit_ignore.json]
    退出码 0=通过 / 1=存在未豁免的高危以上漏洞；豁免清单内漏洞需附理由（audit_ignore.json）
"""
import argparse
import json
import sys


def severity_of(vuln: dict) -> str:
    """从漏洞条目提取严重度（兼容字符串与列表两种形态）。"""
    sev = vuln.get("severity")
    if isinstance(sev, list):
        sev = sev[0] if sev else None
    if not sev:
        return "UNKNOWN"
    return str(sev).upper()


def main() -> int:
    parser = argparse.ArgumentParser(description="pip-audit 审计门禁")
    parser.add_argument("path", nargs="?", default="audit.json", help="pip-audit JSON 输出路径")
    parser.add_argument("--ignore-file", default=None, help="豁免清单 JSON（漏洞 ID -> 理由）")
    args = parser.parse_args()

    ignore: dict[str, str] = {}
    if args.ignore_file:
        try:
            with open(args.ignore_file, encoding="utf-8") as f:
                ignore = json.load(f)
        except FileNotFoundError:
            print(f"ci_audit_gate: 豁免清单 {args.ignore_file} 不存在")
            return 1

    try:
        with open(args.path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"ci_audit_gate: 未找到审计输出文件 {args.path}（pip-audit 执行失败？）")
        return 1

    counts: dict[str, int] = {}
    blocked = 0
    ignored = 0
    total = 0
    details: list[str] = []
    for dep in data.get("dependencies", []):
        name = dep.get("name", "?")
        version = dep.get("version", "?")
        for vuln in dep.get("vulns", []):
            total += 1
            vid = vuln.get("id", "?")
            sev = severity_of(vuln)
            if vid in ignore:
                ignored += 1
                details.append(f"  [IGNORED] {vid} ({name}=={version}): {ignore[vid]}")
                continue
            counts[sev] = counts.get(sev, 0) + 1
            if sev in ("HIGH", "CRITICAL", "UNKNOWN"):
                blocked += 1
                details.append(f"  [{sev}] {name}=={version}: {vid}")
    print(f"ci_audit_gate: 漏洞总数={total} 分布={counts} 阻断数={blocked} 豁免数={ignored}")
    for line in details:
        print(line)
    if blocked:
        print("ci_audit_gate: 存在 HIGH/CRITICAL/UNKNOWN 漏洞，阻断 CI")
        return 1
    print("ci_audit_gate: 通过（无高危以上漏洞）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
