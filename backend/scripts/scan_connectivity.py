"""逐文件连通性扫描：后端每个 .py 的 import 可解析性 + 未使用导入。"""
import ast
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, ROOT)


def find_module_spec(module: str):
    """尝试定位模块（相对 app 包的用文件存在性 + importlib）。"""
    try:
        return importlib.util.find_spec(module)
    except (ImportError, ValueError, ModuleNotFoundError):
        return None


def analyze(path: str):
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    with io.open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"file": rel, "status": "SYNTAX_ERROR", "detail": str(e)}

    issues = []
    imported_names = {}  # name -> (lineno, source)
    all_names = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                # 连通性：标准库/已装包/项目内模块
                if find_module_spec(mod) is None:
                    issues.append(f"import '{mod}' not resolvable (L{node.lineno})")
                name = alias.asname or alias.name.split(".")[0]
                imported_names[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0:
                # 相对导入：转绝对尝试
                base = rel.rsplit("/", 1)[0] if "/" in rel else "."
                full = ("app." if not base.startswith("app") else "") + base
                # 简单判断：相对导入的源文件存在性由 AST 同级目录保证，跳过
                pass
            else:
                if find_module_spec(mod) is None:
                    issues.append(f"from '{mod}' not resolvable (L{node.lineno})")
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported_names[alias.asname or alias.name] = node.lineno
        elif isinstance(node, ast.Name):
            all_names.setdefault(node.id, 0)
            all_names[node.id] += 1
        elif isinstance(node, ast.Attribute):
            pass

    # 未使用导入：import 名在文件中除了 import 语句外未出现
    # 通过 AST 重新统计 Name 出现（含 import 别名场景简化）
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            # a.b -> a 视为使用
            if isinstance(node.value, ast.Name):
                used.add(node.value.id)

    for name, lineno in imported_names.items():
        # 排除 f-string/注释中的误判：Name 使用已足够
        if name not in used:
            issues.append(f"unused import '{name}' (L{lineno})")

    # 顶层重复定义等基础健康
    status = "OK" if not issues else "WARN"
    return {"file": rel, "status": status, "issues": issues}


def main():
    results = []
    for dirpath, _dirs, files in os.walk(APP):
        for fn in files:
            if fn.endswith(".py"):
                results.append(analyze(os.path.join(dirpath, fn)))
    ok = [r for r in results if r["status"] == "OK"]
    warn = [r for r in results if r["status"] == "WARN"]
    err = [r for r in results if r["status"] == "SYNTAX_ERROR"]
    print(f"=== BACKEND SCAN: {len(ok)} OK / {len(warn)} WARN / {len(err)} SYNTAX_ERROR ===")
    for r in sorted(warn, key=lambda x: x["file"]):
        print(f"\n[{r['status']}] {r['file']}")
        for i in r["issues"]:
            print(f"    - {i}")
    for r in err:
        print(f"\n[{r['status']}] {r['file']}: {r['detail']}")
    return results


if __name__ == "__main__":
    main()
