"""Chrome / Edge / Firefox 核心页面与 Widget SDK 兼容性验收。

运行前通过环境变量 ``ZHIHUB_E2E_TOKEN`` 传入短效管理员 JWT；脚本不会打印令牌。
测试目标默认是本机 Nginx ``http://127.0.0.1``，覆盖登录、工作台、知识库、
Widget 预览、运营看板以及第三方来源页加载 ``widget.js``。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import BrowserType, sync_playwright

BASE_URL = os.environ.get("ZHIHUB_E2E_BASE", "http://127.0.0.1").rstrip("/")
TOKEN = os.environ.get("ZHIHUB_E2E_TOKEN", "")
EXTERNAL_HOST = "127.0.0.1"
EXTERNAL_PORT = 19998

ROUTES = (
    ("dashboard", "/", "工作台", True),
    ("knowledge", "/knowledge/list", "知识库文档", True),
    ("widget_preview", "/widget/preview", "双模式预览", True),
    ("analytics", "/analytics/overview", "运营脉搏", True),
)


class _ExternalWidgetPage(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 固定命名
        body = f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>Widget Compatibility</title></head>
<body><h1>第三方网站</h1><script src=\"{BASE_URL}/widget.js\" data-api-key=\"compatibility-dummy\"
data-endpoint=\"{BASE_URL}/api/v1/widget\" data-theme-color=\"#0f8f8c\" async></script></body></html>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def _launch(browser_type: BrowserType, channel: str | None):
    options = {"headless": True}
    if channel:
        options["channel"] = channel
    return browser_type.launch(**options)


def _run_browser(browser_type: BrowserType, *, name: str, channel: str | None, shots: Path) -> dict:
    browser = _launch(browser_type, channel)
    guest_context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    guest_page = guest_context.new_page()
    guest_response = guest_page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=20_000)
    guest_page.get_by_text("登录", exact=True).first.wait_for(state="visible", timeout=10_000)
    route_results = [
        {
            "route": "login",
            "status": guest_response.status if guest_response else None,
            "rendered": "登录" in guest_page.locator("body").inner_text(),
            "authenticated": True,
            "horizontal_overflow_px": guest_page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            ),
        }
    ]
    guest_context.close()

    context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    storage = json.dumps(
        {
            "token": TOKEN,
            "refresh_token": "",
            "user_id": 1,
            "tenant_id": 1,
            "role": "admin",
            "phone": "compatibility-test",
        },
        ensure_ascii=False,
    )
    context.add_init_script(
        f"localStorage.setItem('zhihub:user', {json.dumps(storage)});"
    )
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    request_failures: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "requestfailed",
        lambda request: request_failures.append(request.url)
        if request.resource_type in {"document", "script", "stylesheet", "xhr", "fetch"}
        else None,
    )

    try:
        for route_name, path, expected_text, protected in ROUTES:
            response = page.goto(f"{BASE_URL}{path}", wait_until="networkidle", timeout=20_000)
            page.get_by_text(expected_text, exact=False).first.wait_for(state="visible", timeout=10_000)
            current_url = page.url
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            route_results.append(
                {
                    "route": route_name,
                    "status": response.status if response else None,
                    "rendered": expected_text in page.locator("body").inner_text(),
                    "authenticated": not protected or "/login" not in current_url,
                    "horizontal_overflow_px": overflow,
                }
            )
            if route_name == "analytics":
                page.screenshot(path=str(shots / f"{name}-analytics.png"), full_page=True)

        page.goto(
            f"http://{EXTERNAL_HOST}:{EXTERNAL_PORT}",
            wait_until="networkidle",
            timeout=20_000,
        )
        launcher = page.locator("#zhihub-widget-root .zhihub-widget-launcher")
        launcher.wait_for(state="visible", timeout=10_000)
        launcher.click()
        widget_open = page.locator("#zhihub-widget-root .zhihub-widget-box").evaluate(
            "element => getComputedStyle(element).display === 'flex'"
        )
        theme = launcher.evaluate("element => getComputedStyle(element).backgroundColor")
        page.screenshot(path=str(shots / f"{name}-widget.png"), full_page=True)

        passed = (
            all(
                item["status"] == 200
                and item["rendered"]
                and item["authenticated"]
                and item["horizontal_overflow_px"] <= 1
                for item in route_results
            )
            and widget_open
            and theme == "rgb(15, 143, 140)"
            and not page_errors
            and not console_errors
            and not request_failures
        )
        return {
            "browser": name,
            "version": browser.version,
            "passed": passed,
            "routes": route_results,
            "widget": {"loaded": True, "opened": widget_open, "theme": theme},
            "console_errors": console_errors,
            "page_errors": page_errors,
            "request_failures": request_failures,
        }
    finally:
        context.close()
        browser.close()


def main() -> int:
    if not TOKEN:
        raise SystemExit("ZHIHUB_E2E_TOKEN is required")

    server = ThreadingHTTPServer((EXTERNAL_HOST, EXTERNAL_PORT), _ExternalWidgetPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="zhihub-browser-compat-") as temporary:
            shots = Path(temporary)
            with sync_playwright() as playwright:
                results = [
                    _run_browser(playwright.chromium, name="Chrome", channel="chrome", shots=shots),
                    _run_browser(playwright.chromium, name="Edge", channel="msedge", shots=shots),
                    _run_browser(playwright.firefox, name="Firefox", channel=None, shots=shots),
                ]
            report = {"base_url": BASE_URL, "passed": all(item["passed"] for item in results), "results": results}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
