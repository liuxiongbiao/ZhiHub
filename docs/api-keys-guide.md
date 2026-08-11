# 智枢 ZhiHub API Key 配置指南

> 目的：说明项目需要哪些外部服务密钥、从哪获取、填到哪里、如何验证已切换真实模式。
> 规则：所有密钥写入 `backend/.env`（已被 .gitignore 忽略，**绝不入库**）；`.env.example` 为模板。

## 一、必填（核心能力，缺失则功能不可用）

### DeepSeek（LLM 对话）
- 环境变量：`DEEPSEEK_API_KEY`
- 获取：https://platform.deepseek.com -> API Keys
- 验证：对话回复正常生成（非报错/降级）

### SiliconFlow（Embedding / Rerank）
- 环境变量：`EMBEDDING_API_KEY`
- 获取：https://cloud.siliconflow.cn -> 账户 -> API 密钥
- 验证：知识文档上传后状态变 `ready`（非 failed）

## 二、可选（不填则对应功能 mock/降级）

### 支付宝沙箱（支付闭环真实化）
- 环境变量：`ALIPAY_APP_ID`、`ALIPAY_APP_PRIVATE_KEY`、`ALIPAY_PUBLIC_KEY`
- 获取：https://open.alipay.com/develop/sandbox/app -> 沙箱应用（AppID + 应用私钥/支付宝公钥）
- 不填行为：`alipay_client` mock 模式——生成沙箱跳转 URL（`?mock=1`），回调验签与真实资金流转不可用
- 验证：填 key 后 `POST /billing/pay/{id}` 返回的 URL 不再含 `mock=1`，沙箱支付回调能验签成功

### 微信公众号（微信扫码登录真实化）
- 环境变量：`WECHAT_APP_ID`、`WECHAT_APP_SECRET`
- 获取：https://mp.weixin.qq.com -> 设置与开发 -> 基本配置（需已认证公众号，网页授权需配置授权回调域名）
- 不填行为：`wechat_client` mock 模式——授权 URL 为假地址，`exchange_code_for_openid` 返回 `mock_openid_{code}`
- 验证：登录页点"微信扫码登录"跳转真实微信授权页，扫码后能换到真实 openid 并登录

### 百度 OCR（扫描件/图片 PDF 识别）
- 环境变量：`OCR_API_KEY`
- 获取：https://console.bce.baidu.com/ai/#/ai/ocr/overview/index -> 文字识别 -> 创建应用
- 不填行为：mock 模式——`recognize` 返回空串，扫描件/图片型 PDF 上传报"无法识别"并置文档 failed（**不会污染知识库**）
- 验证：上传扫描 PDF 后文档变 `ready` 且有真实切片

## 三、可观测性（可选）

### Langfuse
- 环境变量：`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL`
- 获取：https://cloud.langfuse.com（或自托管）
- 不填行为：`observe` 降级 no-op，不影响主流程

## 四、生产必改

- `APP_SECRET_KEY`：当前 `change-me-in-production` 必须替换为随机强密钥（JWT/AES/HMAC 依赖）。

## 五、尚未接入的通道（需新增代码，非填 key 可解）

- **短信验证码**：`/sms/send`、`/code/send` demo 模式直接返回验证码；上线需接阿里云 SMS 等通道。
- **邮件**：密码找回/绑定邮箱的验证码同短信；上线需接 SMTP/邮件服务商。
- 这两类不属于"缺 key"，属于"通道未实现"。

## 六、验证是否切换真实模式的通用方法

1. 填 key 后重启后端（uvicorn 启动时读取 .env）
2. 调用对应功能，观察：
   - 日志中不再出现 `mock`/`Mock`/`degraded` 提示
   - 返回 URL/数据不再含 `mock=1`、`mock_openid` 等占位
3. 对应契约测试在真实模式下的行为应符合预期（当前测试断言 mock 行为，真实模式需人工验证）
