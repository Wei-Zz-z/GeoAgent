// 全局会话状态（轻量响应式 store，后续需要时可平滑迁移到 Pinia）
import { reactive } from 'vue'
import { api, openChatSocket } from '../api/client'

let seq = 0
const uid = () => `m${Date.now()}-${seq++}`

// 每个会话选中的数据表（期次）与智能体记忆在本地，切换会话时不会串台
const DATASET_PREF_KEY = 'geoagent.datasetByConversation'
const AGENT_PREF_KEY = 'geoagent.agentByConversation'

export const chat = reactive({
  conversations: [],
  models: [],
  agents: [],
  agentId: '',
  datasets: [],
  datasetId: '',
  currentId: null,
  currentModel: '',
  messages: [],
  streaming: false,
  error: '',
  _streamAssistant: null,
  _ws: null,
})

function loadPrefs(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || '{}') || {}
  } catch {
    return {}
  }
}

function savePref(key, conversationId, value) {
  if (!conversationId || !value) return
  const prefs = loadPrefs(key)
  prefs[conversationId] = value
  try {
    localStorage.setItem(key, JSON.stringify(prefs))
  } catch {
    // 浏览器禁用本地存储时忽略，仅当次会话生效
  }
}

// 智能体清单：默认取后端给的 default_id（会话旁选择框）
function applyAgents(data) {
  chat.agents = (data && data.agents) || []
  if (!chat.agents.length) {
    chat.agentId = ''
    return
  }
  const fallback = (data && data.default_id) || chat.agents[0].id
  if (!chat.agents.some((a) => a.id === chat.agentId)) chat.agentId = fallback
}

// 选择本轮使用的智能体；选择结果随每轮提问作为 agent 下发（不走 LLM 路由）
function selectAgent(agentId) {
  if (!chat.agents.some((a) => a.id === agentId)) return
  chat.agentId = agentId
  savePref(AGENT_PREF_KEY, chat.currentId, agentId)
}

// 期次（数据表）列表：默认取后端登记的 default_id
function applyDatasets(data) {
  chat.datasets = (data && data.datasets) || []
  if (!chat.datasets.length) {
    chat.datasetId = ''
    return
  }
  const fallback = (data && data.default_id) || chat.datasets[0].id
  if (!chat.datasets.some((d) => d.id === chat.datasetId)) chat.datasetId = fallback
}

// 选择本轮统计使用的数据表；选择结果随每轮提问作为 dataset_id 下发
function selectDataset(datasetId) {
  if (!chat.datasets.some((d) => d.id === datasetId)) return
  chat.datasetId = datasetId
  savePref(DATASET_PREF_KEY, chat.currentId, datasetId)
}

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
  const conversationId = chat.currentId
  if (!conversationId) return
  const data = await api.getMessages(conversationId)
  // 回包到达时可能已经切到别的会话：过期结果必须丢弃，否则会把旧消息画进新会话
  if (chat.currentId !== conversationId) return
  chat.messages = buildRenderMessages(data.messages || [])
}

async function refreshConversations() {
  const data = await api.listConversations()
  chat.conversations = data.conversations || []
}

function disconnect() {
  if (chat._ws) {
    chat._ws.onclose = null
    chat._ws.onmessage = null
    chat._ws.close()
    chat._ws = null
  }
  // 旧会话的流式占位与状态一并丢弃，避免切到新会话后残留
  chat._streamAssistant = null
  chat.streaming = false
}

function connect() {
  const conversationId = chat.currentId
  if (!conversationId) return
  // 事件按会话隔离：旧连接上迟到的事件在切走之后一律丢弃
  const socket = openChatSocket(conversationId, (event) => {
    if (chat.currentId !== conversationId) return
    handleEvent(event)
  })
  chat._ws = socket
  socket.onclose = () => {
    if (chat._ws !== socket) return
    if (chat.streaming) {
      chat.streaming = false
      chat.error = '连接已断开'
    }
  }
  socket.onerror = () => {
    if (chat._ws !== socket) return
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
  const [modelsData, convsData, datasetsData, agentsData] = await Promise.all([
    api.listModels(),
    api.listConversations(),
    api.listDatasets(),
    api.listAgents(),
  ])
  chat.models = modelsData.models || []
  chat.conversations = convsData.conversations || []
  applyDatasets(datasetsData)
  applyAgents(agentsData)
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
  // 详情回包可能晚于下一次切换（新建会话时最容易撞上），此时必须丢弃
  if (chat.currentId !== id) return
  chat.currentModel = conv.model || ''
  // 恢复该会话上次选中的数据表与智能体；没有记录或已失效时回落到默认值
  const savedDataset = loadPrefs(DATASET_PREF_KEY)[id]
  if (savedDataset && chat.datasets.some((d) => d.id === savedDataset)) {
    chat.datasetId = savedDataset
  }
  const savedAgent = loadPrefs(AGENT_PREF_KEY)[id]
  if (savedAgent && chat.agents.some((a) => a.id === savedAgent)) {
    chat.agentId = savedAgent
  }
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
  const conversationId = chat.currentId
  if (!conversationId) return
  await api.switchModel(conversationId, model)
  // 期间可能已切到别的会话：只更新会话列表，不再改动当前会话的模型显示
  const conv = chat.conversations.find((c) => c.id === conversationId)
  if (conv) conv.model = model
  if (chat.currentId !== conversationId) return
  chat.currentModel = model
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

// 套合数据被删除或新增后，重新拉取期次列表（下拉框随之更新；失效的选中项会自动回落默认期次）
async function refreshDatasets() {
  const data = await api.listDatasets()
  applyDatasets(data)
}
function sendMessage(content, origin = null) {
  const text = String(content || '').trim()
  if (!text || chat.streaming || !chat._ws || chat._ws.readyState !== WebSocket.OPEN) return false
  chat.messages.push({ id: uid(), role: 'user', content: text })
  const payload = { type: 'user', content: text, ...(origin || {}) }
  // 显式带上当前选中的数据表（期次）：后端优先按它决定统计哪张表
  if (!payload.dataset_id && chat.datasetId) payload.dataset_id = chat.datasetId
  // 显式带上当前选择的智能体：后端直接交给它，不再做 LLM 路由
  if (!payload.agent && chat.agentId) payload.agent = chat.agentId
  chat._ws.send(JSON.stringify(payload))
  return true
}

chat.init = init
chat.selectConversation = selectConversation
chat.createConversation = createConversation
chat.deleteConversation = deleteConversation
chat.switchModel = switchModel
chat.selectDataset = selectDataset
chat.selectAgent = selectAgent
chat.editAndResend = editAndResend
chat.sendMessage = sendMessage
chat.refreshMessages = refreshMessages
chat.refreshDatasets = refreshDatasets
