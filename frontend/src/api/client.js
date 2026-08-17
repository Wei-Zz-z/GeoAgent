// 后端 API 封装：REST 走相对路径（经 Vite 代理），对话走 WebSocket 流式事件
const BASE = '/api'

async function request(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
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
  listModels: () => request('/models'),
  listConversations: () => request('/conversations'),
  createConversation: (payload) =>
    request('/conversations', { method: 'POST', body: JSON.stringify(payload) }),
  getConversation: (id) => request(`/conversations/${id}`),
  getMessages: (id) => request(`/conversations/${id}/messages`),
  switchModel: (id, model) =>
    request(`/conversations/${id}/model`, { method: 'PUT', body: JSON.stringify({ model }) }),
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
