// 全部后端接口封装（路径经 Vite proxy 转发到 FastAPI）
// 登录后后端所有业务接口都有保镖（401）——所以每个请求都要带 token。
// 统一走 apiFetch：自动贴 Authorization 头 + 401 兜底（token 失效 → 清登录态 → 回登录页）。

import { getToken, clearToken } from '../auth'

const BASE = '/api/v1'

/** 统一请求：自动加 Bearer token 头（与后端脚本里 _sess.headers["Authorization"] 同理） */
function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return fetch(url, { ...options, headers })
}

async function _json(res) {
  if (!res.ok) {
    if (res.status === 401) {
      // token 过期/被顶号：本地已不可信，清掉并广播——App.vue 收到后回登录页
      clearToken()
      window.dispatchEvent(new CustomEvent('auth-expired'))
    }
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch (_) { /* 非 JSON 响应 */ }
    throw new Error(detail)
  }
  return res.json()
}

// ---------- 文档 ----------
export const uploadDocument = (file) => {
  const form = new FormData()
  form.append('file', file)
  return apiFetch(`${BASE}/documents/upload`, { method: 'POST', body: form }).then(_json)
}

export const listDocuments = () => apiFetch(`${BASE}/documents`).then(_json)
export const deleteDocument = (docId) =>
  apiFetch(`${BASE}/documents/${docId}`, { method: 'DELETE' }).then(_json)

// ---------- 会话 ----------
export const listSessions = () => apiFetch(`${BASE}/sessions`).then(_json)
export const deleteSession = (threadId) =>
  apiFetch(`${BASE}/sessions/${threadId}`, { method: 'DELETE' }).then(_json)

export const getSessionMessages = (threadId) =>
  apiFetch(`${BASE}/sessions/${threadId}/messages`).then(_json)
