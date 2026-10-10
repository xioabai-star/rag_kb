<script setup>
// 会话列表：展示 / 切换 / 删除（删除 = 后端级联清除 checkpointer 与业务表）
defineProps({
  sessions: { type: Array, default: () => [] },
  activeThreadId: { type: String, default: '' },
})
const emit = defineEmits(['select', 'new-session', 'delete'])

const remove = (s) => {
  if (!confirm(`删除会话「${s.title}」？历史消息将一并清除。`)) return
  emit('delete', s.thread_id)
}
</script>

<template>
  <div class="panel">
    <div class="panel-header">
      <h3>会话</h3>
      <button class="new-btn" @click="emit('new-session')">＋ 新建</button>
    </div>

    <div class="session-list">
      <div
        v-for="s in sessions"
        :key="s.thread_id"
        class="session-item"
        :class="{ active: s.thread_id === activeThreadId }"
        @click="emit('select', s.thread_id)"
      >
        <div class="session-title">{{ s.title }}</div>
        <div class="session-time">{{ s.updated_at }}</div>
        <button class="del-btn" title="删除" @click.stop="remove(s)">✕</button>
      </div>

      <div v-if="!sessions.length" class="empty">暂无会话，点击「新建」开始提问</div>
    </div>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; height: 100%; }
.panel-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid #eef0f3; }
.panel-header h3 { font-size: 15px; font-weight: 600; }
.new-btn { background: #e8f0fe; color: #2563eb; padding: 4px 10px; border-radius: 6px; font-size: 13px; }
.new-btn:hover { background: #dbe7fd; }
.session-list { flex: 1; overflow-y: auto; padding: 4px 8px; }
.session-item { position: relative; padding: 9px 26px 9px 10px; border-radius: 6px; cursor: pointer; }
.session-item:hover { background: #f2f3f5; }
.session-item.active { background: #e8f0fe; }
.session-title { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-time { color: #86909c; font-size: 12px; margin-top: 2px; }
.del-btn { position: absolute; right: 6px; top: 50%; transform: translateY(-50%); color: #c9cdd4; padding: 2px 6px; border-radius: 4px; opacity: 0; }
.session-item:hover .del-btn { opacity: 1; }
.del-btn:hover { color: #dc2626; background: #fee2e2; }
.empty { text-align: center; color: #a8abb2; padding: 32px 0; font-size: 13px; }
</style>
