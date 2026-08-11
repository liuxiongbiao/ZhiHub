# backend/Dockerfile
# 职责: 多阶段构建 — 安装依赖 → 复制源码 → 生产就绪
# 分工: 🟢 弱模型可独立完成
# 对应日程: Day 9

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
# 生产启动命令（无 --reload，单worker）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
