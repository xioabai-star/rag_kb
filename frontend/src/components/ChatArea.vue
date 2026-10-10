<script setup>
// 聊天区：消息流 + 流式打字 + 引用溯源（折叠展示）+ 输入发送
import { ref, nextTick, watch } from 'vue'

// 气泡状态徽标映射（status → 样式类 + 文案）：加新状态只改这里，不动模板
// tool_map 复用 tool 样式类：报销与地图同为"查询类"结果，同一视觉系别
const BADGES = {
  ok: { cls: 'ok', label: '已检索知识库' },
  no_info: { cls: 'no_info', label: '未命中知识库' },
  chitchat: { cls: 'chitchat', label: '闲聊' },
  tool: { cls: 'tool', label: '已查询业务数据' },
  tool_map: { cls: 'tool', label: '已查询地图服务' },
}

const props = defineProps({
  messages: { type: Array, default: () => [] }, // [{role: 'user'|'assistant', content, status, references}]
  isStreaming: { type: Boolean, default: false },
})
const emit = defineEmits(['send'])

const input = ref('')
const listEl = ref(null)
const expandedRefs = ref(new Set()) // 展开的引用块序号

const send = () => {
  const text = input.value.trim()
  if (!text || props.isStreaming) return
  input.value = ''
  emit('send', text)
}

const toggleRefs = (idx) => {
  const s = new Set(expandedRefs.value)
  s.has(idx) ? s.delete(idx) : s.add(idx)
  expandedRefs.value = s
}

// 新消息/流式追加时自动滚到底
watch(
  () => props.messages.map((m) => m.content).join('|'),
  async () => {
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  }
)

const onKeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="chat">
    <div ref="listEl" class="msg-list">
      <div v-if="!messages.length" class="welcome">
        <h2>企业知识库问答</h2>
        <p>上传企业文档后，向我提问制度、流程相关问题（支持多轮对话与引用溯源）。</p>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
        <div class="avatar">{{ m.role === 'user' ? '我' : 'AI' }}</div>
        <div class="bubble">
          <div class="content">{{ m.content }}<span v-if="isStreaming && i === messages.length - 1" class="cursor">▍</span></div>

          <!-- 引用溯源：折叠展示 -->
          <div v-if="m.references && m.references.length" class="refs">
            <button class="ref-toggle" @click="toggleRefs(i)">
              引用来源（{{ m.references.length }}）
              <span class="arrow">{{ expandedRefs.has(i) ? '▲' : '▼' }}</span>
            </button>
            <div v-if="expandedRefs.has(i)" class="ref-list">
              <div v-for="r in m.references" :key="r.index" class="ref-item">
                <span class="ref-no">[{{ r.index }}]</span>
                <span class="ref-name">{{ r.filename }} {{ r.header }}</span>
                <div class="ref-snippet">{{ r.content }}</div>
              </div>
            </div>
          </div>

          <div v-if="m.status" class="status-badge">
            <span class="badge" :class="(BADGES[m.status] || BADGES.ok).cls">
              {{ (BADGES[m.status] || BADGES.ok).label }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="input-bar">
      <textarea
        v-model="input"
        rows="2"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        @keydown="onKeydown"
      ></textarea>
      <button class="send-btn" :disabled="isStreaming || !input.trim()" @click="send">
        {{ isStreaming ? '生成中…' : '发送' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat { display: flex; flex-direction: column; height: 100%; }
.msg-list { flex: 1; overflow-y: auto; padding: 20px 24px; }
.welcome { text-align: center; margin-top: 18vh; color: #86909c; }
.welcome h2 { color: #1f2329; margin-bottom: 8px; }

.msg-row { display: flex; gap: 10px; margin-bottom: 18px; }
.msg-row.user { flex-direction: row-reverse; }
.avatar { width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0; }
.msg-row.user .avatar { background: #2563eb; color: #fff; }
.msg-row.assistant .avatar { background: #e8f0fe; color: #2563eb; }

.bubble { max-width: 72%; padding: 10px 14px; border-radius: 10px; line-height: 1.7; }
.msg-row.user .bubble { background: #2563eb; color: #fff; border-top-right-radius: 2px; }
.msg-row.assistant .bubble { background: #fff; border: 1px solid #eef0f3; border-top-left-radius: 2px; }
.content { white-space: pre-wrap; word-break: break-word; }
.cursor { color: #2563eb; animation: blink 0.8s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.refs { margin-top: 8px; border-top: 1px dashed #e5e7eb; padding-top: 6px; }
.ref-toggle { color: #2563eb; font-size: 12px; display: inline-flex; gap: 4px; align-items: center; }
.arrow { font-size: 10px; }
.ref-list { margin-top: 6px; display: flex; flex-direction: column; gap: 6px; }
.ref-item { background: #f8f9fb; border-radius: 6px; padding: 6px 10px; font-size: 12px; color: #4e5969; }
.ref-no { color: #2563eb; font-weight: 600; margin-right: 4px; }
.ref-name { font-weight: 600; }
.ref-snippet { margin-top: 3px; color: #86909c; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.status-badge { margin-top: 8px; }

.input-bar { display: flex; gap: 10px; padding: 14px 20px; border-top: 1px solid #eef0f3; background: #fff; }
.input-bar textarea { flex: 1; resize: none; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; font: inherit; outline: none; line-height: 1.5; }
.input-bar textarea:focus { border-color: #2563eb; }
.send-btn { align-self: flex-end; background: #2563eb; color: #fff; padding: 9px 22px; border-radius: 8px; }
.send-btn:disabled { background: #c7d2fe; cursor: not-allowed; }
</style>
