// 登录态管理：localStorage 存 token + 登录/注册/登出/启动校验
// （前端版"持久会话"——对应后端脚本里 requests.Session 的思想：token 只拿一次，处处复用）

const TOKEN_KEY = 'ragkb_token'
const BASE = '/api/v1'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

async function _detail(res) {
  try {
    const body = await res.json()
    if (body.detail) return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
  } catch (_) { /* 非 JSON */ }
  return `HTTP ${res.status}`
}

/** 登录：表单提交（login 是 OAuth2 表单接口，兼容 Swagger Authorize——后端设计定死的） */
export async function login(username, password) {
  const form = new URLSearchParams() // 表单格式：username=xx&password=xx
  form.append('username', username)
  form.append('password', password)
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  })
  if (!res.ok) throw new Error(await _detail(res))
  const data = await res.json()
  setToken(data.access_token) // 通行证到手，存 localStorage
  return data.user
}

/** 注册：只造账号不发 token（职责分离）；前端注册成功后再调一次 login 自动登录 */
export async function register(username, password) {
  const res = await fetch(`${BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, role: 'employee' }), // UI 注册一律 employee；admin 走 Swagger/脚本
  })
  if (!res.ok) throw new Error(await _detail(res))
  return res.json()
}

/** 启动校验：token 还在吗、是谁？无效返回 null（App 据此决定进主界面还是登录页） */
export async function fetchMe() {
  const token = getToken()
  if (!token) return null
  const res = await fetch(`${BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) return null
  return res.json()
}
