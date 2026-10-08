// 全局会话状态（轻量响应式 store，后续需要时可平滑迁移到 Pinia）
import { reactive } from 'vue'
import { api, openChatSocket } from '../api/client'

let seq = 0
const uid = () => `m${Date.now()}-${seq++}`

export const chat = reactive({
  conversations: [],
  models: [],
  currentId: null,
  currentModel: '',
  messages: [],
  streaming: false,
  error: '',
  _streamAssistant: null,
  _ws: null,
})

function parseArgs(raw) {
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw)
    } catch {
      return raw
    }
  }
  return raw
}

// 将服务端持久化的消息（含 tool_calls / artifacts）组装为渲染结构：
// 每个 assistant 消息下挂工具卡片，tool 消息按 tool_call_id 归入对应卡片
function buildRenderMessages(rawMessages) {
  const out = []
  let assistant = null
  for (const m of rawMessages) {
    if (m.role === 'user') {
      assistant = null
      out.push({ id: uid(), role: 'user', content: m.content || '' })
    } else if (m.role === 'assistant') {
      assistant = {
        id: uid(),
        role: 'assistant',
        // 带工具调用的 assistant 消息里的文字只是过程话术，正文由紧随其后的
        // 最终 assistant 消息展示，这里不再重复显示，避免与工具卡片抢版面。
        content: (m.tool_calls || []).length ? '' : m.content || '',
        streaming: false,
        route: m.route || '',
        answerOrigin: m.answer_origin || '',
        subagents: Array.isArray(m.subagents) ? m.subagents : [],
        toolCalls: (m.tool_calls || []).map((tc) => ({
          id: tc.id,
          name: tc.function?.name || 'tool',
          args: parseArgs(tc.function?.arguments),
          status: 'done',
          result: '',
          artifacts: [],
        })),
        artifacts: [],
      }
      out.push(assistant)
    } else if (m.role === 'tool') {
      const card = assistant?.toolCalls.find((c) => c.id === m.tool_call_id)
      const artifactList = Array.isArray(m.artifacts) ? m.artifacts : []
      if (card) {
        card.result = m.content || ''
        card.status = String(m.content || '').startsWith('Error') ? 'error' : 'done'
        card.artifacts.push(...artifactList)
      } else {
        // 孤立 tool 消息（跨轮次残留）：作为独立条目展示
        out.push({
          id: uid(),
          role: 'tool-standalone',
          content: m.content || '',
          artifacts: artifactList,
        })
      }
    }
  }
  return out
}

async function refreshMessages() {
  if (!chat.currentId) return
  const data = await api.getMessages(chat.currentId)
  chat.messages = buildRenderMessages(data.messages || [])
}

async function refreshConversations() {
  const data = await api.listConversations()
  chat.conversations = data.conversations || []
}

function disconnect() {
  if (chat._ws) {
    chat._ws.onclose = null
    chat._ws.close()
    chat._ws = null
  }
}

function connect() {
  if (!chat.currentId) return
  chat._ws = openChatSocket(chat.currentId, handleEvent)
  chat._ws.onclose = () => {
    if (chat.streaming) {
      chat.streaming = false
      chat.error = '连接已断开'
    }
  }
  chat._ws.onerror = () => {
    chat.error = 'WebSocket 连接失败，请确认后端已启动'
  }
}

function handleEvent(event) {
  switch (event.type) {
    case 'turn_start':
      chat.streaming = true
      chat.error = ''
      chat._streamAssistant = {
        id: uid(),
        role: 'assistant',
        content: '',
        streaming: true,
        route: '',
        answerOrigin: event.answer_origin || '',
        sawTool: false,
        subagents: [],
        toolCalls: [],
        artifacts: [],
      }
      chat.messages.push(chat._streamAssistant)
      break
    case 'route':
      if (chat._streamAssistant) chat._streamAssistant.route = event.target || ''
      break
    case 'subagent_start':
      if (chat._streamAssistant) {
        chat._streamAssistant.subagents.push({
          id: event.id,
          prompt: event.prompt || '',
          status: 'running',
          content: '',
        })
      }
      break
    case 'subagent_end': {
      const sub = chat._streamAssistant?.subagents.find((s) => s.id === event.id)
      if (sub) {
        sub.status = event.is_error ? 'error' : 'done'
        sub.content = event.content || ''
      }
      break
    }
    case 'token':
      if (chat._streamAssistant) chat._streamAssistant.content += event.delta || ''
      break
    case 'tool_call':
      if (chat._streamAssistant) {
        // 工具调用开始后，前面可能已流出的是模型"准备查询"的过程话术，
        // 不作为最终回复展示，避免过程话术与工具执行后流式输出的结论正文混在一起。
        if (!chat._streamAssistant.sawTool) {
          chat._streamAssistant.content = ''
          chat._streamAssistant.sawTool = true
        }
        chat._streamAssistant.toolCalls.push({
          id: event.id,
          name: event.name,
          args: event.arguments || {},
          status: 'running',
          result: '',
          artifacts: [],
        })
      }
      break
    case 'tool_result': {
      const card = chat._streamAssistant?.toolCalls.find((c) => c.id === event.id)
      if (card) {
        card.status = event.is_error ? 'error' : 'done'
        card.result = event.content || ''
      }
      break
    }
    case 'artifact':
      if (chat._streamAssistant) {
        const artifact = { kind: event.kind, name: event.name, data: event.data }
        // 优先挂到刚执行完（或正在执行）的工具卡片下
        const target = [...chat._streamAssistant.toolCalls].reverse().find((c) => c.status !== 'running')
        if (target) target.artifacts.push(artifact)
        else chat._streamAssistant.artifacts.push(artifact)
      }
      break
    case 'message':
      if (chat._streamAssistant) chat._streamAssistant.content = event.content || ''
      break
    case 'error':
      chat.error = event.message || '未知错误'
      chat.streaming = false
      break
    case 'turn_end':
      if (chat._streamAssistant) chat._streamAssistant.streaming = false
      chat._streamAssistant = null
      chat.streaming = false
      refreshMessages().catch((err) => {
        chat.error = err.message
      })
      // 首条消息后标题可能已由服务端自动生成，刷新会话列表。
      refreshConversations().catch((err) => {
        chat.error = err.message
      })
      break
  }
}

async function init() {
  const [modelsData, convsData] = await Promise.all([api.listModels(), api.listConversations()])
  chat.models = modelsData.models || []
  chat.conversations = convsData.conversations || []
  if (!chat.conversations.length) {
    const conv = await api.createConversation({ title: '新会话' })
    chat.conversations.unshift(conv)
  }
  await selectConversation(chat.conversations[0].id)
}

async function selectConversation(id) {
  if (chat.currentId === id && chat._ws) return
  disconnect()
  chat.currentId = id
  chat.messages = []
  chat.streaming = false
  chat.error = ''
  const conv = await api.getConversation(id)
  chat.currentModel = conv.model || ''
  chat.messages = buildRenderMessages(conv.messages || [])
  connect()
}

async function createConversation() {
  const now = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  const conv = await api.createConversation({ title: `新会话 ${now}` })
  chat.conversations.unshift(conv)
  await selectConversation(conv.id)
}

async function deleteConversation(id) {
  await api.deleteConversation(id)
  const idx = chat.conversations.findIndex((c) => c.id === id)
  if (idx >= 0) chat.conversations.splice(idx, 1)
  if (chat.currentId !== id) return
  disconnect()
  chat.currentId = null
  chat.messages = []
  chat.streaming = false
  if (chat.conversations.length) {
    await selectConversation(chat.conversations[0].id)
  } else {
    const conv = await api.createConversation({ title: '新会话' })
    chat.conversations.unshift(conv)
    await selectConversation(conv.id)
  }
}

async function switchModel(model) {
  if (!chat.currentId) return
  await api.switchModel(chat.currentId, model)
  chat.currentModel = model
  const conv = chat.conversations.find((c) => c.id === chat.currentId)
  if (conv) conv.model = model
}

// 修改最后一条用户问题：先撤掉服务端旧问题与旧回答，再以新内容重新发起一轮
async function editAndResend(content) {
  const text = String(content || '').trim()
  if (!text || !chat.currentId || chat.streaming) return false
  try {
    await api.rollbackLastUserTurn(chat.currentId)
  } catch (err) {
    chat.error = err.message
    return false
  }
  await refreshMessages()
  refreshConversations().catch(() => {})
  return sendMessage(text)
}

function sendMessage(content, origin = null) {
  const text = String(content || '').trim()
  if (!text || chat.streaming || !chat._ws || chat._ws.readyState !== WebSocket.OPEN) return false
  chat.messages.push({ id: uid(), role: 'user', content: text })
  chat._ws.send(JSON.stringify({ type: 'user', content: text, ...(origin || {}) }))
  return true
}

chat.init = init
chat.selectConversation = selectConversation
chat.createConversation = createConversation
chat.deleteConversation = deleteConversation
chat.switchModel = switchModel
chat.editAndResend = editAndResend
chat.sendMessage = sendMessage
chat.refreshMessages = refreshMessages
