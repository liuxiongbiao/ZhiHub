# 浏览器兼容性验收报告（2026-08-24）

## 环境与口径

- 目标：本机 Docker Compose 全栈，经 Nginx `http://127.0.0.1` 访问。
- 浏览器：Google Chrome 151.0.7922.170、Microsoft Edge 151.0.4129.101、Playwright Firefox 132.0。
- 视口：1440×900，语言环境 `zh-CN`。
- 页面：登录、工作台、知识库、Widget 预览、运营看板。
- Widget：独立第三方来源页（`127.0.0.1:19998`）跨源加载生产 `/widget.js`，打开在线咨询浮层并验证主题色。
- 失败条件：HTTP 非 200、关键标题未渲染、鉴权页被重定向登录、横向溢出、控制台异常、页面异常、document/script/stylesheet/xhr/fetch 请求失败、Widget 未加载/打不开/主题失真。

## 结果

| 检查 | Chrome 151 | Edge 151 | Firefox 132 |
|---|---|---|---|
| 登录页 | ✅ 200 / 正常渲染 / 0px 横向溢出 | ✅ | ✅ |
| 工作台 | ✅ 200 / 鉴权正常 / 0px 横向溢出 | ✅ | ✅ |
| 知识库 | ✅ 200 / 鉴权正常 / 0px 横向溢出 | ✅ | ✅ |
| Widget 预览 | ✅ 200 / 鉴权正常 / 0px 横向溢出 | ✅ | ✅ |
| 运营看板 | ✅ 200 / 鉴权正常 / 0px 横向溢出 | ✅ | ✅ |
| 第三方页加载并打开 widget.js | ✅，主题 `rgb(15, 143, 140)` | ✅ | ✅ |
| 控制台 / 页面异常 | 0 / 0 | 0 / 0 | 0 / 0 |
| 关键网络请求失败 | 0 | 0 | 0 |

结论：三浏览器全部通过，未发现需要修复的兼容性问题。该测试覆盖当前实际安装版本；“每个浏览器最新两个大版本”的持续覆盖仍应由 BrowserStack/Sauce Labs 等浏览器矩阵在发布流水线中承担，本机一次性验收不虚构未安装版本结果。

## 复测

先签发一个短效本地管理员 JWT 到环境变量（不得提交或打印），然后运行：

```powershell
cd backend
$env:ZHIHUB_E2E_TOKEN='<short-lived local admin JWT>'
.venv\Scripts\python.exe ..\frontend\tests\e2e\browser_compatibility.py
```

脚本截图写入系统临时目录并在退出时自动删除，不会留下测试文件；测试只读页面与 Widget 展开状态，不创建业务数据。
