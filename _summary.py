# -*- coding: utf-8 -*-
"""提取后端 app 与前端 src 每文件符号摘要（供逐文件审查报告）。"""
import ast, io, os, re

def summarize_py(p):
    try:
        tree = ast.parse(io.open(p, encoding='utf-8-sig').read())
    except Exception as e:
        return f"[PARSE ERR {e}]"
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name != '__init__']
    return f"cls={len(classes)} fn={len(funcs)}"

def summarize_ts(p):
    s = io.open(p, encoding='utf-8').read()
    funcs = re.findall(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)', s)
    exports = re.findall(r'export\s+(?:default\s+)?(?:function|const|class|interface|type)\s+(\w+)', s)
    comps = re.findall(r'<script setup', s)
    return f"fn={len(funcs)} export={len(exports)}"

for label, root, ext in [('BACKEND', 'C:/Users/Lenovo/Desktop/Three/zhihub/backend/app', '.py'),
                          ('FRONTEND', 'C:/Users/Lenovo/Desktop/Three/zhihub/frontend/src', None)]:
    print(f'### {label}')
    for dirpath, _d, files in os.walk(root):
        for fn in sorted(files):
            if ext and not fn.endswith(ext): continue
            if not ext and not (fn.endswith('.ts') or fn.endswith('.vue')): continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace('\\', '/')
            lines = sum(1 for _ in io.open(p, encoding='utf-8'))
            info = summarize_py(p) if ext else summarize_ts(p)
            print(f'{rel}|{lines}|{info}')
