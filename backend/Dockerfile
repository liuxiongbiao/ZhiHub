# backend/Dockerfile
# 职责: 多阶段构建 — 安装依赖 → 复制源码 → 生产就绪

# ─── 构建阶段: 安装依赖 ───
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── 运行阶段: 最小镜像 ───
FROM python:3.12-slim
WORKDIR /app
# 从构建阶段复制已安装的包（避免在运行阶段装pip依赖）
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY . .
EXPOSE 8000
# 生产启动命令（无 --reload，双 worker 提升并发吞吐）
# --proxy-headers: 信任 nginx 注入的 X-Forwarded-For 获取真实客户端 IP（P1-8）
# 注意: backend 端口已收敛为 127.0.0.1 映射，仅 nginx 可经内网访问
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*"]
