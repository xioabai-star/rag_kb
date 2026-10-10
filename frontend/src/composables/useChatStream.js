// SSE 流式聊天：fetch + ReadableStream 手动拆帧（SSE 协议：event/data 帧，\n\n 分隔）
//
// 事件协议（与后端对齐）：
//   session     → 新会话的 thread_id
//   status      → 意图（kb_question/chitchat）/ 回答状态（ok/no_info）
//   message     → 流式 token（逐字追加）
//   references  → 引用溯源表
//   done        → 流结束（含完整回答与引用）
//   error       → 异常
import { ref } from 'vue'
import { getToken } from '../auth'

export function useChatStream() {
  const isStreaming = ref(false)
  const error = ref('')

  /**
   * 发起流式问答。handlers: { session, status, message, references, done, error }
   * 返回最终 thread_id（新会话时由 session 帧确定）。
   */
  async function streamChat(message, threadId, handlers) {
    isStreaming.value = true
    error.value = ''
    try {
      const headers = { 'Content-Type': 'application/json' }
      const token = getToken()
      if (token) headers.Authorization = `Bearer ${token}` // 聊天接口同样受保护
      const res = await fetch('/api/v1/chat/stream', {
        method: 'POST',
        headers,
        body: JSON.stringify({ message, thread_id: threadId || null }),
      })
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = '' // 半帧缓冲：网络分片可能把一帧拆到两次 read

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按 \n\n 边界拆出完整帧
        let idx
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const raw = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          dispatchFrame(raw, handlers)
        }
      }
    } catch (e) {
      error.value = e.message || String(e)
      handlers?.error?.({ error: error.value })
    } finally {
      isStreaming.value = false
    }
  }

  function dispatchFrame(raw, handlers) {
    let event = 'message'
    let data = ''
    for (const line of raw.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data += line.slice(5).trim()
    }
    if (!data) return
    try {
      const payload = JSON.parse(data)
      if (event === 'message' && payload.content) handlers?.message?.(payload)
      else if (event === 'status') handlers?.status?.(payload)
      else if (event === 'session') handlers?.session?.(payload)
      else if (event === 'references') handlers?.references?.(payload)
      else if (event === 'done') handlers?.done?.(payload)
      else if (event === 'error') handlers?.error?.(payload)
    } catch (_) {
      /* 坏帧丢弃，不中断流 */
    }
  }

  return { isStreaming, error, streamChat }
}
