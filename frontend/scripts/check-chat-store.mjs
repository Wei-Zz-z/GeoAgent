// 会话切换竞态回归：切换 / 新建会话时，旧会话的异步返回（或旧 WebSocket 事件）
// 不能写进新会话的视图。用 stub 的 fetch / WebSocket 在 Node 里直接跑 chat store，
// 不需要后端；`node scripts/check-chat-store.mjs` 退出码非 0 即失败。
import assert from 'node:assert/strict'

// ---------------------------------------------------------------- stub 环境
globalThis.location = { protocol: 'http:', host: 'localhost:5173' }

class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  constructor(url) {
    this.url = url
    this.readyState = FakeWebSocket.OPEN
    this.sent = []
    sockets.push(this)
  }
  send(data) { this.sent.push(data) }
  close() { this.readyState = 3; this.onclose = null }
}

const sockets = []
const gates = new Map() // path -> {promise, release}
const conversations = new Map()
let created = 0

function gate(path) {
  let release
  const promise = new Promise((resolve) => { release = resolve })
  gates.set(path, { promise, release })
  return () => { gates.delete(path); release() }
}

function deferredJson(payload, path) {
  const entry = gates.get(path)
  if (!entry) return Promise.resolve(response(payload))
  return entry.promise.then(() => response(payload))
}

function response(payload) {
  return { ok: true, status: 200, statusText: 'OK', json: async () => payload }
}

globalThis.WebSocket = FakeWebSocket
globalThis.fetch = async (url, options = {}) => {
  const path = String(url)
  const method = (options.method || 'GET').toUpperCase()
  if (method === 'POST' && path === '/api/conversations') {
    const body = JSON.parse(options.body || '{}')
    created += 1
    const id = `new${created}`
    const conv = { id, title: body.title || '新会话', model: 'm', created_at: '', updated_at: '' }
    conversations.set(id, { conv, messages: [] })
    return response(conv)
  }
  const messages = path.match(/^\/api\/conversations\/([^/]+)\/messages$/)
  if (method === 'GET' && messages) {
    const id = messages[1]
    return deferredJson({ messages: conversations.get(id)?.messages || [] }, path)
  }
  const detail = path.match(/^\/api\/conversations\/([^/]+)$/)
  if (method === 'GET' && detail) {
    const id = detail[1]
    const item = conversations.get(id) || { conv: { id, title: id, model: 'm' }, messages: [] }
    return deferredJson({ ...item.conv, messages: item.messages }, path)
  }
  if (path === '/api/conversations') {
    return response({ conversations: [...conversations.values()].map((item) => item.conv) })
  }
  if (path === '/api/models') return response({ models: [] })
  if (path === '/api/agents') return response({ agents: [], default_id: '' })
  if (path === '/api/datasets') return response({ datasets: [], default_id: '' })
  return response({})
}

const { chat } = await import('../src/stores/chat.js')

function seedConversation(id, title, messages) {
  conversations.set(id, {
    conv: { id, title, model: 'm', created_at: '', updated_at: '' },
    messages,
  })
}

const OLD_MESSAGES = [
  { role: 'user', content: '旧会话的问题' },
  { role: 'assistant', content: '旧会话的回答' },
]

// ---------------------------------------------------------------- 场景一：切换会话时，新会话先返回
async function caseSwitchRace() {
  conversations.clear()
  resetView()
  seedConversation('A', '旧会话', OLD_MESSAGES)
  const releaseA = gate('/api/conversations/A')

  const pendingSelect = chat.selectConversation('A')   // 慢：A 的详情挂起
  await new Promise((r) => setTimeout(r, 0))
  await chat.createConversation()                      // 快：新建并切到新会话
  assert.equal(chat.currentId, 'new1', '新建后应停在新会话')
  assert.equal(chat.messages.length, 0, '新会话应为空')

  releaseA()                                           // 旧会话详情这时才返回
  await pendingSelect.catch(() => {})
  await new Promise((r) => setTimeout(r, 0))

  assert.equal(chat.currentId, 'new1', '旧会话返回后不应改变当前会话')
  assert.equal(chat.messages.length, 0,
    `新会话被旧会话的消息污染了（当前 ${chat.messages.length} 条）`)
}

// ---------------------------------------------------------------- 场景二：刷新消息回包晚于切换
async function caseRefreshRace() {
  conversations.clear()
  resetView()
  seedConversation('A', '旧会话', OLD_MESSAGES)
  seedConversation('B', '新会话', [])
  await chat.selectConversation('A')
  assert.equal(chat.messages.length, OLD_MESSAGES.length, '先进入旧会话')

  const releaseMsg = gate('/api/conversations/A/messages')
  const pendingRefresh = chat.refreshMessages()        // 慢：A 的消息挂起
  await new Promise((r) => setTimeout(r, 0))
  await chat.selectConversation('B')                   // 切到空会话
  releaseMsg()
  await pendingRefresh.catch(() => {})
  await new Promise((r) => setTimeout(r, 0))

  assert.equal(chat.currentId, 'B')
  assert.equal(chat.messages.length, 0,
    `刷新回包把旧会话消息写进了新会话（当前 ${chat.messages.length} 条）`)
}

// ---------------------------------------------------------------- 场景三：旧 WebSocket 事件不得串台
async function caseStaleSocketEvents() {
  conversations.clear()
  resetView()
  seedConversation('A', '旧会话', OLD_MESSAGES)
  seedConversation('B', '新会话', [])
  await chat.selectConversation('A')
  const stale = sockets[sockets.length - 1]
  await chat.selectConversation('B')
  const before = chat.messages.length

  stale.onmessage?.({ data: JSON.stringify({ type: 'token', delta: '旧会话残留' }) })
  stale.onmessage?.({ data: JSON.stringify({ type: 'turn_start', conversation_id: 'A' }) })
  await new Promise((r) => setTimeout(r, 0))

  assert.equal(chat.messages.length, before, '旧会话的流式事件不应出现在新会话视图里')
  assert.equal(chat.streaming, false, '旧会话事件不应把新会话置为流式中')
}

function resetView() {
  chat.messages = []
  chat.currentId = null
  chat.streaming = false
  chat._streamAssistant = null
  if (chat._ws) { chat._ws.onclose = null; chat._ws.close(); chat._ws = null }
}

const cases = [
  ['切换会话竞态', caseSwitchRace],
  ['刷新消息竞态', caseRefreshRace],
  ['旧连接事件串台', caseStaleSocketEvents],
]
let failed = 0
for (const [name, run] of cases) {
  try {
    await run()
    console.log(`✓ ${name}`)
  } catch (error) {
    failed += 1
    console.error(`✗ ${name}: ${error.message}`)
  }
}
console.log(failed ? `\n会话切换回归：${failed} 项失败` : '\n会话切换回归：全部通过')
process.exit(failed ? 1 : 0)