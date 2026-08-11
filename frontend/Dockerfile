# frontend/Dockerfile
# 职责: 多阶段构建 — npm build → nginx serve
# 分工: 🟢 弱模型可独立完成
# 对应日程: Day 9

FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
