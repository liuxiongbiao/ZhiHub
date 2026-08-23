# Quickstart: 知识库上传 + 混合检索引擎 验收验证

**Phase 1 Output** | **Date**: 2026-08-04

## Prerequisites

1. Docker Compose 中间件全部 healthy：MySQL、Redis、RabbitMQ、Milvus、Elasticsearch
2. Backend 运行中：`cd zhihub/backend && uvicorn app.main:app --reload --port 8000`
3. Celery Worker 运行中：`cd zhihub/backend && celery -A celery_worker worker --loglevel=info`
4. Frontend 运行中：`cd zhihub/frontend && npm run dev`
5. 已注册一个租户账号并获取 JWT token

## 环境变量配置

在 `zhihub/backend/.env` 中确认以下配置：
```env
EMBEDDING_PROVIDER=siliconflow
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_MODEL=BAAI/bge-m3
RERANK_MODEL=BAAI/bge-reranker-v2-m3
DEEPSEEK_API_KEY=sk-xxx
MILVUS_HOST=localhost
MILVUS_PORT=19530
ELASTICSEARCH_URL=http://localhost:9200
```

## 运行 Alembic Migration

```bash
cd zhihub/backend
alembic revision --autogenerate -m "day3_knowledge_document"
alembic upgrade head
```

验证 `knowledge_documents` 表已创建：
```bash
mysql -h localhost -P 3307 -u zhihub -pzhihub zhihub -e "DESC knowledge_documents;"
```

## 初始化 Milvus Collection & ES Index

```bash
cd zhihub/backend
python -c "
from app.core.embedding_client import init_milvus_collection, init_es_index
init_milvus_collection()
init_es_index()
print('OK: Milvus collection + ES index created')
"
```

## 验收场景

### 场景 1: 上传文档（对应 User Story 1）

```bash
# 准备测试文件
echo '# 产品使用手册\n\n## 如何升级套餐\n\n在个人中心点击「套餐管理」，选择目标套餐并确认支付即可完成升级。\n\n## 退款政策\n\n购买后7天内可申请全额退款，超过7天按使用天数折算。' > /tmp/test_doc.md

# 上传文档（替换 YOUR_TOKEN）
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/tmp/test_doc.md"

# 轮询状态
curl http://localhost:8000/api/v1/knowledge/list?status=ready \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**验收**: 文档状态最终变为 `ready`，`chunk_count > 0`。

### 场景 2: 检索测试台对比（对应 User Story 2）

```bash
# 精确查询（BM25 优势场景）
curl -X POST http://localhost:8000/api/v1/knowledge/test \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "退款政策", "history": []}'

# 语义查询（向量优势场景）
curl -X POST http://localhost:8000/api/v1/knowledge/test \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "怎么把套餐换成更贵的", "history": []}'
```

**验收**: 精确查询时混合结果的 BM25 来源片段排名靠前；语义查询时向量结果与混合结果有差异。

### 场景 3: 空知识库（对应 User Story 2 Edge Case）

```bash
# 用新注册的租户（未上传文档）测试
curl -X POST http://localhost:8000/api/v1/knowledge/test \
  -H "Authorization: Bearer NEW_TENANT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "如何升级套餐", "history": []}'
```

**验收**: `"empty_knowledge": true`，不触发 LLM 调用。

### 场景 4: 异常情况验证

**上传不支持格式**:
```bash
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/tmp/test.exe"
# 预期: 400, "不支持的文件格式"
```

**上传超限文件**:
```bash
dd if=/dev/zero of=/tmp/big_file.pdf bs=1M count=11
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/tmp/big_file.pdf"
# 预期: 400, "文件大小超过限制"
```

### 场景 5: 版本管理（User Story 4）

```bash
# 第一次上传
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/tmp/test_doc.md"

# 修改文件后再次上传同名文件
echo '# 产品使用手册\n\n## 如何升级套餐\n\n新版：在首页点击「升级」按钮即可。' > /tmp/test_doc.md
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/tmp/test_doc.md"

# 验证旧版本 is_latest=false
curl http://localhost:8000/api/v1/knowledge/list \
  -H "Authorization: Bearer YOUR_TOKEN"
# 预期: 旧版本 is_latest=false, 新版本 is_latest=true, version=2
```

**验收**: 检索时只返回新版本文档的切片内容。

## 运行测试

```bash
cd zhihub/backend
pytest tests/unit/test_file_parser.py tests/unit/test_retrieval_service.py -v
pytest tests/integration/test_knowledge_api.py tests/integration/test_knowledge_tasks.py -v

cd zhihub/frontend
npx vitest run tests/components/KnowledgeList.spec.ts tests/components/SearchTest.spec.ts
```
