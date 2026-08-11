"""前端 import 路径连通性扫描：解析每个 .ts/.vue 的 import，检查目标存在。"""
import io
import os
import re

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")

IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:type\s+)?[\w{}*,\s]+\s+from\s+['"]([^'"]+)['"]|^\s*import\s+['"]([^'"]+)['"]|^\s*import\s+type\s+[\w{}*,\s]+\s+from\s+['"]([^'"]+)['"]""",
    re.M,
)


def resolve(base_dir: str, spec: str):
    """把 import 说明符解析为存在的文件，返回 (ok, resolved_or_reason)。"""
    if spec.startswith("@/"):
        p = os.path.join(SRC, spec[2:])
    elif spec.startswith("@") or spec.startswith("element"):
        return True, "external/npm"
    elif spec.startswith("."):
        p = os.path.normpath(os.path.join(base_dir, spec))
    else:
        return True, "external/bare"
    # 尝试扩展名
    candidates = [p, p + ".ts", p + ".vue", p + ".d.ts", os.path.join(p, "index.ts")]
    for c in candidates:
        if os.path.isfile(c):
            return True, os.path.relpath(c, SRC).replace("\\", "/")
    return False, spec


def main():
    results = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not (fn.endswith(".ts") or fn.endswith(".vue")):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, SRC).replace("\\", "/")
            try:
                src = io.open(p, encoding="utf-8").read()
            except Exception as e:
                results.append((rel, "READ_ERROR", str(e)))
                continue
            specs = [m[0] or m[1] or m[2] for m in IMPORT_RE.findall(src)]
            problems = []
            for spec in specs:
                if not spec:
                    continue
                ok, detail = resolve(dirpath, spec)
                if not ok:
                    problems.append(f"'{spec}' -> NOT FOUND")
            results.append((rel, "OK" if not problems else "WARN", "; ".join(problems)))

    ok = [r for r in results if r[1] == "OK"]
    warn = [r for r in results if r[1] == "WARN"]
    err = [r for r in results if r[1] == "READ_ERROR"]
    print(f"=== FRONTEND SCAN: {len(ok)} OK / {len(warn)} WARN / {len(err)} ERR ===")
    for rel, st, d in sorted(warn, key=lambda x: x[0]):
        print(f"[{st}] {rel}: {d}")
    for rel, st, d in err:
        print(f"[{st}] {rel}: {d}")


if __name__ == "__main__":
    main()
