// 后端 API 封装：REST 走相对路径（经 Vite 代理），对话走 WebSocket 流式事件
const BASE = '/api'

// 会话过期（任意 /api 返回 401）时的回调，由 src/auth.js 注册
let onUnauthorized = null

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler
}

async function request(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    // 登录态走 HttpOnly Cookie：同源请求默认携带，显式声明避免后续改动踩坑
    credentials: 'same-origin',
    ...options,
  })
  if (resp.status === 401 && onUnauthorized) onUnauthorized()
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    try {
      const data = await resp.json()
      if (data.detail) detail = data.detail
    } catch {
      // 非 JSON 错误体，保留默认信息
    }
    throw new Error(detail)
  }
  return resp.json()
}

export const api = {
  health: () => request('/health'),
  // 入口验证：当前登录用户 / 用省域平台 ticket 换登录态 / 退出登录
  me: () => request('/auth/me'),
  loginWithTicket: (ticket) =>
    request('/auth/ticket', { method: 'POST', body: JSON.stringify({ ticket }) }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  listModels: () => request('/models'),
  listAgents: () => request('/agents'),
  listDatasets: () => request('/datasets'),
  listConversations: () => request('/conversations'),
  createConversation: (payload) =>
    request('/conversations', { method: 'POST', body: JSON.stringify(payload) }),
  getConversation: (id) => request(`/conversations/${id}`),
  getMessages: (id) => request(`/conversations/${id}/messages`),
  deleteConversation: (id) =>
    request(`/conversations/${id}`, { method: 'DELETE' }),
  switchModel: (id, model) =>
    request(`/conversations/${id}/model`, { method: 'PUT', body: JSON.stringify({ model }) }),
  rollbackLastUserTurn: (id) =>
    request(`/conversations/${id}/rollback-last-user-turn`, { method: 'POST' }),
}

// 打开会话 WebSocket；onEvent 收到服务端下发的流式事件
export function openChatSocket(conversationId, onEvent) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/conversations/${conversationId}/ws`)
  ws.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data))
    } catch {
      // 忽略无法解析的事件
    }
  }
  return ws
}
