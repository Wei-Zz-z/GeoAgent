<template>
  <main v-if="chat.currentId" class="chat">
    <header class="chat-head">
      <div class="chat-title-block">
        <div class="chat-title">{{ currentTitle }}</div>
        <div v-if="chat.currentModel" class="chat-subtitle">
          {{ shortModel(chat.currentModel) }} · {{ formatWhen(currentConv?.updated_at) }}
        </div>
      </div>
      <div class="chat-head-actions">
        <div ref="agentEl" class="model-picker">
          <button
            class="model-btn agent-btn"
            :title="currentAgent ? currentAgent.description : '选择本轮使用的智能体'"
            :disabled="chat.streaming"
            @click="agentOpen = !agentOpen"
          >
            <span class="agent-dot"></span>
            <span class="model-text">{{ currentAgent ? currentAgent.label : '选择智能体' }}</span>
            <Icon name="chevron-down" :size="14" />
          </button>
          <div v-if="agentOpen" class="model-menu agent-menu">
            <div
              v-for="a in chat.agents"
              :key="a.id"
              class="model-opt"
              :class="{ active: a.id === chat.agentId }"
              @click="onPickAgent(a)"
            >
              <span class="model-name">{{ a.label }}</span>
              <span v-if="a.id === chat.agentId" class="model-note">使用中</span>
            </div>
            <div v-if="currentAgent" class="agent-hint">{{ currentAgent.description }}</div>
          </div>
        </div>
        <span v-if="chat.streaming" class="live-badge"><span class="dot"></span>分析中</span>
      </div>
    </header>

    <div v-if="chat.error" class="error-banner">
      <span>{{ chat.error }}</span>
      <button class="icon-btn" title="关闭" @click="chat.error = ''">
        <Icon name="x" :size="14" />
      </button>
    </div>

    <div class="workspace-bar">
      <button class="workspace-btn county" type="button" :disabled="chat.streaming" @click="openWorkspace('county')">
        <span class="workspace-icon"><Icon name="chart" :size="16" /></span>
        <span class="workspace-text">
          <strong>区县地图</strong>
          <small>上传图斑，按区县出面积统计图</small>
        </span>
      </button>
      <button class="workspace-btn overlay" type="button" :disabled="chat.streaming" @click="openWorkspace('overlay')">
        <span class="workspace-icon"><Icon name="database" :size="16" /></span>
        <span class="workspace-text">
          <strong>套合入库</strong>
          <small>前后时相套合成一期数据表</small>
        </span>
      </button>
    </div>

    <div ref="messagesEl" class="messages">
      <div class="msg-list">
        <div v-if="!chat.messages.length" class="welcome">
          <div class="welcome-icon"><Icon name="search" :size="24" /></div>
          <div class="welcome-title">有什么可以帮你？</div>
          <div class="welcome-sub">输入自然语言，进行土地变化查询、统计分析与快报生成</div>
          <div class="suggestions">
            <button
              v-for="s in suggestions"
              :key="s"
              class="suggest-chip"
              :disabled="chat.streaming"
              @click="sendSuggestion(s)"
            >
              {{ s }}
            </button>
          </div>
        </div>
        <MessageBubble
          v-for="(m, i) in chat.messages"
          :key="m.id"
          :message="m"
          :can-edit="i === lastUserIndex && !chat.streaming"
        />
      </div>
    </div>

    <footer class="composer-area">
      <div class="composer">
        <div class="composer-scope">
          <div class="dataset-block">
            <span class="dataset-label">数据表</span>
            <div ref="datasetEl" class="model-picker">
              <button
                class="model-btn dataset-btn"
                :title="currentDataset ? currentDataset.table : '选择本轮统计使用的数据表'"
                :disabled="chat.streaming"
                @click="datasetOpen = !datasetOpen"
              >
                <span class="dataset-dot"></span>
                <span class="model-text">{{ currentDataset ? currentDataset.label : '默认数据表' }}</span>
                <Icon name="chevron-down" :size="14" />
              </button>
              <div v-if="datasetOpen" class="model-menu dataset-menu">
                <div
                  v-for="d in chat.datasets"
                  :key="d.id"
                  class="model-opt"
                  :class="{ active: d.id === chat.datasetId }"
                  @click="onPickDataset(d)"
                >
                  <span class="model-name">{{ d.label }}</span>
                  <span class="model-note">{{ d.id === chat.datasetId ? '使用中' : d.table }}</span>
                </div>
              </div>
            </div>
          </div>
          <span class="scope-hint">面积按亩统计</span>
        </div>
        <textarea
          ref="ta"
          v-model="draft"
          rows="1"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          :disabled="chat.streaming"
          @keydown.enter.exact.prevent="onSend"
          @input="autosize"
        />
        <div class="composer-bar">
          <div class="composer-left">
            <button class="icon-btn" title="添加附件" :disabled="chat.streaming" @click="onAttach">
              <Icon name="paperclip" :size="17" />
            </button>

            <div ref="pickerEl" class="model-picker">
              <button
                class="model-btn"
                :title="'当前模型：' + (chat.currentModel || '未选择')"
                :disabled="chat.streaming"
                @click="pickerOpen = !pickerOpen"
              >
                <span class="model-dot"></span>
                <span class="model-text">{{ shortModel(chat.currentModel) || '选择模型' }}</span>
                <Icon name="chevron-down" :size="14" />
              </button>
              <div v-if="pickerOpen" class="model-menu">
                <div
                  v-for="m in chat.models"
                  :key="m.id"
                  class="model-opt"
                  :class="{ active: m.id === chat.currentModel, disabled: !m.available }"
                  @click="onPickModel(m)"
                >
                  <span class="model-name">{{ m.id }}</span>
                  <span v-if="m.id === chat.currentModel" class="model-note">使用中</span>
                  <span v-else-if="!m.available" class="model-note">未配置</span>
                </div>
              </div>
            </div>

            <span v-if="tip" class="composer-tip">{{ tip }}</span>
          </div>

          <button
            class="send-btn"
            title="发送"
            :disabled="chat.streaming || !draft.trim()"
            @click="onSend"
          >
            <Icon v-if="!chat.streaming" name="send" :size="17" />
            <span v-else class="spinner"></span>
          </button>
        </div>
      </div>
    </footer>
  </main>

  <main v-else class="chat chat-empty">从左侧选择或新建一个会话</main>

  <AppModal
    :open="workspace === 'county'"
    title="区县地图统计"
    subtitle="上传变化图斑（可选行政区边界），生成按区县的面积统计图；不调用数据库与模型。"
    @close="closeWorkspace"
  >
    <CountyMapWorkspace embedded />
  </AppModal>

  <AppModal
    :open="workspace === 'overlay'"
    title="套合入库"
    subtitle="上传后时相、前时相（必填）与永久基本农田、生态保护红线（可选），套合生成一期数据表并登记期次。"
    width="min(1400px, 96vw)"
    @close="closeWorkspace"
  >
    <OverlayWorkspace embedded />
  </AppModal>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './Icon.vue'
import MessageBubble from './MessageBubble.vue'
import AppModal from './AppModal.vue'
import CountyMapWorkspace from './CountyMapWorkspace.vue'
import OverlayWorkspace from './OverlayWorkspace.vue'
import { chat } from '../stores/chat'
import { formatWhen, shortModel } from '../display'

const draft = ref('')
const tip = ref('')
const messagesEl = ref(null)
const ta = ref(null)
const pickerEl = ref(null)
const pickerOpen = ref(false)
const datasetEl = ref(null)
const datasetOpen = ref(false)
const agentEl = ref(null)
const agentOpen = ref(false)
// 会话窗口上方的工作台入口：区县地图 / 套合入库，统一用大弹窗承载，不做页面跳转
const workspace = ref('')

function openWorkspace(name) {
  if (chat.streaming) return
  workspace.value = name
}

function closeWorkspace() {
  workspace.value = ''
}
let tipTimer = null

// 示例问题：养老场景未启用时（后端智能体清单里没有 elder_care）不展示对应示例
const suggestions = computed(() => {
  const items = [
    '耕地转为建设用地的面积是多少？',
    '按三大类统计各类土地变化面积',
    '生成本期土地变化监测快报',
  ]
  if (chat.agents.some((a) => a.id === 'elder_care')) {
    items.push('分析养老机构的步行可达性')
  }
  return items
})

const currentConv = computed(
  () => chat.conversations.find((c) => c.id === chat.currentId) || null,
)
const currentTitle = computed(() => currentConv.value?.title || '新对话')
// 当前选中的数据表（期次）：决定本轮统计查哪张套合表
const currentDataset = computed(
  () => chat.datasets.find((d) => d.id === chat.datasetId) || null,
)
// 会话旁选择的本轮智能体：后端直接交给它，不再做 LLM 路由
const currentAgent = computed(
  () => chat.agents.find((a) => a.id === chat.agentId) || null,
)

// 只有最后一条用户问题允许“修改并重新生成”
const lastUserIndex = computed(() => {
  for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
    if (chat.messages[i].role === 'user') return i
  }
  return -1
})

// 消息变化时（流式 token 逐字追加）自动滚动到底部
watch(
  () => [chat.messages.length, chat.messages[chat.messages.length - 1]?.content],
  async () => {
    await nextTick()
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  },
)

// 模型 / 数据表菜单打开时，点击菜单外部自动收起
watch(pickerOpen, (open) => {
  if (open) {
    document.addEventListener('mousedown', onOutside)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('mousedown', onOutside)
    document.removeEventListener('keydown', onKeydown)
  }
})

watch(datasetOpen, (open) => {
  if (open) {
    document.addEventListener('mousedown', onOutsideDataset)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('mousedown', onOutsideDataset)
    document.removeEventListener('keydown', onKeydown)
  }
})

watch(agentOpen, (open) => {
  if (open) {
    document.addEventListener('mousedown', onOutsideAgent)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('mousedown', onOutsideAgent)
    document.removeEventListener('keydown', onKeydown)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onOutside)
  document.removeEventListener('mousedown', onOutsideDataset)
  document.removeEventListener('mousedown', onOutsideAgent)
  document.removeEventListener('keydown', onKeydown)
  if (tipTimer) clearTimeout(tipTimer)
})

function onOutside(e) {
  if (pickerEl.value && !pickerEl.value.contains(e.target)) pickerOpen.value = false
}

function onOutsideDataset(e) {
  if (datasetEl.value && !datasetEl.value.contains(e.target)) datasetOpen.value = false
}

function onOutsideAgent(e) {
  if (agentEl.value && !agentEl.value.contains(e.target)) agentOpen.value = false
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    pickerOpen.value = false
    datasetOpen.value = false
    agentOpen.value = false
  }
}

function autosize() {
  if (!ta.value) return
  ta.value.style.height = 'auto'
  ta.value.style.height = Math.min(ta.value.scrollHeight, 160) + 'px'
}

function sendSuggestion(text) {
  if (chat.sendMessage(text)) {
    pickerOpen.value = false
    datasetOpen.value = false
    nextTick(autosize)
  }
}

function onSend() {
  const text = draft.value
  if (!text.trim() || !chat.sendMessage(text)) return
  pickerOpen.value = false
  datasetOpen.value = false
  agentOpen.value = false
  draft.value = ''
  if (ta.value) ta.value.style.height = 'auto'
}

function onPickDataset(d) {
  datasetOpen.value = false
  chat.selectDataset(d.id)
}

function onPickAgent(a) {
  agentOpen.value = false
  chat.selectAgent(a.id)
}

async function onPickModel(m) {
  pickerOpen.value = false
  if (!m.available || m.id === chat.currentModel) return
  try {
    await chat.switchModel(m.id)
  } catch (err) {
    chat.error = err.message
  }
}

function onAttach() {
  tip.value = '附件上传功能暂未开放'
  if (tipTimer) clearTimeout(tipTimer)
  tipTimer = setTimeout(() => {
    tip.value = ''
  }, 2500)
}
</script>
