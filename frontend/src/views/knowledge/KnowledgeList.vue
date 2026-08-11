<!-- frontend/src/views/knowledge/KnowledgeList.vue -->
<!-- 职责: 知识库文档列表 - 状态筛选/上传入口(012 前端打磨:PageHeader + el-card) -->
<template>
  <div>
    <PageHeader title="知识库文档" subtitle="管理知识库文档,支持 PDF / Word / Markdown">
      <template #actions>
        <el-upload
          :before-upload="handleBeforeUpload"
          :http-request="handleUpload"
          :show-file-list="false"
          accept=".pdf,.docx,.md"
        >
          <el-button type="primary">
            <el-icon><Upload /></el-icon>&nbsp;上传文档
          </el-button>
        </el-upload>
      </template>
    </PageHeader>

    <div class="page-container">
      <el-card shadow="never">
        <div class="toolbar">
          <el-select
            v-model="statusFilter"
            placeholder="全部状态"
            clearable
            style="width: 160px"
            @change="fetchList"
          >
            <el-option label="待处理" value="pending" />
            <el-option label="处理中" value="processing" />
            <el-option label="已就绪" value="ready" />
            <el-option label="处理失败" value="failed" />
          </el-select>
        </div>

        <el-table :data="documents" v-loading="loading" stripe>
          <el-table-column prop="filename" label="文件名" min-width="200" />
          <el-table-column prop="file_type" label="格式" width="80" />
          <el-table-column label="大小" width="100">
            <template #default="{ row }">
              {{ formatFileSize(row?.file_size) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="docStatusType(row?.status)" size="small">{{ DOC_STATUS_MAP[row?.status] || row?.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="version" label="版本" width="70" />
          <el-table-column prop="chunk_count" label="切片数" width="80" />
          <el-table-column label="上传时间" width="180">
            <template #default="{ row }">
              {{ formatDate(row?.uploaded_at) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button
                v-if="row?.status === 'failed'"
                type="warning"
                size="small"
                @click="handleRebuild(row?.doc_id)"
                :loading="rebuildingId === row?.doc_id"
              >
                重建索引
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="fetchList"
          />
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { getDocumentList, uploadDocument, rebuildDocument, type DocumentItem } from '@/api/knowledge'
import PageHeader from '@/components/common/PageHeader.vue'

/** 文档状态 -> 中文标签 */
const DOC_STATUS_MAP: Record<string, string> = {
  pending: '待处理', processing: '处理中', ready: '已就绪', failed: '处理失败',
}
/** 文档状态 -> el-tag 配色 */
function docStatusType(status?: string): string {
  return { pending: 'warning', processing: 'primary', ready: 'success', failed: 'danger' }[status ?? ''] || 'info'
}

const documents = ref<DocumentItem[]>([])
const loading = ref(false)
const statusFilter = ref<string | undefined>(undefined)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const rebuildingId = ref<number | null>(null)

/** 字节数 -> 人类可读大小（B/KB/MB） */
function formatFileSize(bytes?: number): string {
  if (bytes === undefined || bytes === null) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

/** ISO 时间串 -> 中文本地化时间 */
function formatDate(iso: string): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

/** 拉取文档列表（带状态筛选 + 分页），失败提示 */
async function fetchList() {
  loading.value = true
  try {
    const res = await getDocumentList({
      status: statusFilter.value,
      page: page.value,
      page_size: pageSize.value,
    })
    documents.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e?.message || '加载文档列表失败')
  } finally {
    loading.value = false
  }
}

/** 上传前校验: 格式（pdf/docx/md）+ 大小（≤10MB），不通过返回 false 中断 */
function handleBeforeUpload(file: File): boolean {
  const allowed = ['.pdf', '.docx', '.md']
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!allowed.includes(ext)) {
    ElMessage.error(`不支持的文件格式：${ext}，仅支持 PDF、Word、Markdown`)
    return false
  }
  if (file.size > 10 * 1024 * 1024) {
    ElMessage.error('文件大小超过 10MB 限制')
    return false
  }
  return true
}

/** 自定义上传请求: 调 uploadDocument 后刷新列表 */
async function handleUpload({ file }: { file: File }) {
  try {
    await uploadDocument(file)
    ElMessage.success('上传成功，正在处理中...')
    fetchList()
  } catch (e: any) {
    ElMessage.error(e?.message || '上传失败')
  }
}

/** 重建指定文档索引，期间按钮 loading */
async function handleRebuild(docId: number) {
  rebuildingId.value = docId
  try {
    await rebuildDocument(docId)
    ElMessage.success('重建索引任务已提交')
    fetchList()
  } catch (e: any) {
    ElMessage.error(e?.message || '重建失败')
  } finally {
    rebuildingId.value = null
  }
}

onMounted(() => {
  fetchList()
})
</script>
