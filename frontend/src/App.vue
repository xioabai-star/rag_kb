<script setup>
// 根组件：认证闸门 + 双栏布局（左：文档库 + 会话；右：聊天区）
// 状态中枢：会话列表 / 当前会话 / 消息流 / SSE 事件分发
// 闸门逻辑：currentUser 为空 → 只渲染 LoginView（数据接口全部 401，没必要进去）
//           启动时用 token 换一次用户信息（fetchMe），token 失效自动回登录页
import { ref, onMounted } from 'vue'
import { listSessions, deleteSession, getSessionMessages } from './api'
import { fetchMe, clearToken } from './auth'
import { useChatStream } from './composables/useChatStream'
import DocumentList from './components/DocumentList.vue'
import SessionList from './components/SessionList.vue'
import ChatArea from './components/ChatArea.vue'
import LoginView from './components/LoginView.vue'

const currentUser = ref(null) // null = 未登录，显示登录页
const booting = ref(true) // 启动校验期间不闪登录页（token 还在但校验没回来）
const sessions = ref([])
const activeThreadId = ref('')
const messages = ref([]) // [{role, content, status, references}]
const { isStreaming, error, streamChat } = useChatStream()

const loadSessions = async () => {
  const data = await listSessions()
  sessions.value = data.sessions || []
}

// 启动：localStorage 有 token 就先换用户信息（顺便验 token 是否还有效）
const boot = async () => {
  const me = await fetchMe()
  if (me) {
    currentUser.value = me
    await loadSessions()
  }
  booting.value = false
}

// 登录成功（LoginView 发来 authed 事件）
const onAuthed = async (user) => {
  currentUser.value = user
  await loadSessions()
}

const logout = () => {
  clearToken()
  currentUser.value = null
  sessions.value = []
  activeThreadId.value = ''
  messages.value = []
}

// token 中途失效（接口 401 → api/index.js 广播 auth-expired）
const onAuthExpired = () => {
  currentUser.value = null
  sessions.value = []
  activeThreadId.value = ''
  messages.value = []
}

// 发送：立即入列（用户消息 + 空 AI 消息），SSE token 逐字追加实现打字机
// 注意：不能持有 push 前的原始对象去改——ref([]) 的元素是懒包装的 proxy，
// 改 raw 对象不触发响应式。必须通过 messages.value 索引访问（拿到 proxy）再改。
const send = (text) => {
  messages.value.push({ role: 'user', content: text, status: '' })
  messages.value.push({ role: 'assistant', content: '', status: '', references: [] })

  streamChat(text, activeThreadId.value, {
    session: (p) => {
      // 新会话：记下 thread_id，刷新左侧列表
      activeThreadId.value = p.thread_id
      loadSessions()
    },
    message: (p) => {
      messages.value[messages.value.length - 1].content += p.content
    },
    status: (p) => {
      // 意图帧（kb_question/tool_request）只是"要去哪个分支"的预告，不进气泡；
      // 气泡显示的是完成帧状态（ok / no_info / chitchat / tool）
      messages.value[messages.value.length - 1].status =
        ['kb_question', 'tool_request'].includes(p.status) ? '' : p.status
    },
    references: (p) => {
      messages.value[messages.value.length - 1].references = p.references || []
    },
    done: (p) => {
      // no_info 等无 token 流的分支：用 done 帧回填完整回答
      const aiMsg = messages.value[messages.value.length - 1]
      if (!aiMsg.content && p.content) aiMsg.content = p.content
    },
    error: (p) => {
      const aiMsg = messages.value[messages.value.length - 1]
      aiMsg.content = (aiMsg.content || '') + `\n[错误] ${p.error || '未知错误'}`
    },
  }).finally(() => {
    loadSessions() // 流结束刷新会话时间排序
  })
}

// 切换会话：读 checkpointer 历史
const selectSession = async (threadId) => {
  if (isStreaming.value) return
  activeThreadId.value = threadId
  const data = await getSessionMessages(threadId)
  messages.value = (data.messages || []).map((m) => ({
    role: m.role,
    content: m.content,
    status: '',
    references: [],
  }))
}

const newSession = () => {
  if (isStreaming.value) return
  activeThreadId.value = ''
  messages.value = []
}

const removeSession = async (threadId) => {
  await deleteSession(threadId)
  if (threadId === activeThreadId.value) newSession()
  loadSessions()
}

onMounted(() => {
  boot()
  window.addEventListener('auth-expired', onAuthExpired)
})
</script>

<template>
  <!-- 未登录：登录页整屏（后端接口全 401，主界面进不去也白搭） -->
  <LoginView v-if="!booting && !currentUser" @authed="onAuthed" />

  <div v-else-if="!booting" class="layout">
    <aside class="sidebar">
      <div class="user-bar">
        <span class="user-name">{{ currentUser.username }}</span>
        <span class="user-role">{{ currentUser.role }}</span>
        <button class="logout-btn" @click="logout">退出</button>
      </div>
      <DocumentList />
      <div class="divider"></div>
      <SessionList
        :sessions="sessions"
        :active-thread-id="activeThreadId"
        @select="selectSession"
        @new-session="newSession"
        @delete="removeSession"
      />
    </aside>
    <main class="chat-area">
      <ChatArea :messages="messages" :is-streaming="isStreaming" @send="send" />
    </main>
  </div>
</template>

<style scoped>
.layout { display: flex; height: 100vh; }
.sidebar {
  width: 300px;
  background: #fff;
  border-right: 1px solid #eef0f3;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.sidebar > :nth-child(2) { flex: 0 0 auto; max-height: 45%; } /* DocumentList 限高（原 :first-child 规则被 user-bar 顶位了） */
.divider { border-top: 1px solid #eef0f3; }
.sidebar > :last-child { flex: 1; min-height: 0; }
.chat-area { flex: 1; min-width: 0; background: #f5f6f8; }
.user-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  flex: 0 0 auto;
  border-bottom: 1px solid #eef0f3;
  background: #fff;
}
.user-name { font-size: 14px; font-weight: 600; color: #1f2329; }
.user-role {
  font-size: 12px;
  color: #2563eb;
  background: #e8f0fe;
  border-radius: 4px;
  padding: 1px 6px;
}
.logout-btn {
  margin-left: auto;
  font-size: 12px;
  color: #86909c;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 3px 8px;
}
.logout-btn:hover { color: #dc2626; border-color: #dc2626; }
</style>
