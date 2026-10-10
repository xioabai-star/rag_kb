<script setup>
// 文档管理：上传 / 列表 / 删除。上传后每 3s 轮询刷新（后台异步入库，状态从 processing → ready/failed）
import { ref, onMounted, onUnmounted } from 'vue'
import { uploadDocument, listDocuments, deleteDocument } from '../api'

const documents = ref([])
const uploading = ref(false)

const refresh = async () => {
  const data = await listDocuments()
  documents.value = data.documents || []
}

const onPick = async (e) => {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  uploading.value = true
  try {
    await uploadDocument(file)
    await refresh()
  } finally {
    uploading.value = false
  }
}

const remove = async (doc) => {
  if (!confirm(`删除文档「${doc.filename}」？知识库将同步移除其全部内容。`)) return
  await deleteDocument(doc.doc_id)
  await refresh()
}

const fmtSize = (n) => (n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB')

let timer = null
onMounted(() => {
  refresh()
  timer = setInterval(refresh, 3000) // 轮询跟踪后台入库状态
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="panel">
    <div class="panel-header">
      <h3>知识库文档</h3>
      <label class="upload-btn">
        {{ uploading ? '上传中…' : '+ 上传' }}
        <input type="file" accept=".md,.txt,.pdf,.docx" hidden @change="onPick" />
      </label>
    </div>

    <div class="doc-list">
      <div v-for="doc in documents" :key="doc.doc_id" class="doc-item">
        <div class="doc-info">
          <div class="doc-name" :title="doc.filename">{{ doc.filename }}</div>
          <div class="doc-meta">
            <span class="badge" :class="doc.status">{{ doc.status }}</span>
            <span v-if="doc.chunk_count">· {{ doc.chunk_count }} 块</span>
            <span>· {{ fmtSize(doc.size) }}</span>
          </div>
        </div>
        <button class="del-btn" title="删除" @click="remove(doc)">✕</button>
      </div>

      <div v-if="!documents.length" class="empty">暂无文档，点击右上角上传企业资料</div>
    </div>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; height: 100%; }
.panel-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid #eef0f3; }
.panel-header h3 { font-size: 15px; font-weight: 600; }
.upload-btn { background: #2563eb; color: #fff; padding: 5px 12px; border-radius: 6px; font-size: 13px; }
.upload-btn:hover { background: #1d4ed8; }
.doc-list { flex: 1; overflow-y: auto; padding: 4px 8px; }
.doc-item { display: flex; align-items: center; justify-content: space-between; padding: 8px 6px; border-bottom: 1px solid #f2f3f5; }
.doc-item:hover { background: #f8f9fb; }
.doc-info { min-width: 0; }
.doc-name { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.doc-meta { margin-top: 3px; display: flex; gap: 6px; align-items: center; color: #86909c; font-size: 12px; }
.del-btn { color: #c9cdd4; font-size: 13px; padding: 2px 6px; border-radius: 4px; }
.del-btn:hover { color: #dc2626; background: #fee2e2; }
.empty { text-align: center; color: #a8abb2; padding: 32px 0; font-size: 13px; }
</style>
