<template>
  <div class="msg" :class="message.role">
    <div v-if="message.role === 'user'" class="user-msg">
      <div v-if="!editing" class="user-row">
        <div class="bubble user-bubble">{{ message.content }}</div>
      </div>
      <div v-else class="user-row">
        <div class="edit-box">
          <textarea
            ref="editInput"
            v-model="editDraft"
            rows="2"
            placeholder="修改问题"
            @keydown.enter.exact.prevent="submitEdit"
            @keydown.esc="cancelEdit"
          />
          <div class="edit-actions">
            <span class="edit-hint">修改后将重新生成回答</span>
            <button class="btn btn-ghost" @click="cancelEdit">取消</button>
            <button class="btn btn-primary" :disabled="!editDraft.trim()" @click="submitEdit">
              发送
            </button>
          </div>
        </div>
      </div>
      <div v-if="!editing" class="msg-actions">
        <button class="action-btn" :title="copied ? '已复制' : '复制'" @click="copyText">
          <Icon :name="copied ? 'check' : 'copy'" :size="13" />
        </button>
        <button v-if="canEdit" class="action-btn" title="修改并重新生成" @click="startEdit">
          <Icon name="edit" :size="13" />
        </button>
      </div>
    </div>

    <div
      v-else-if="message.role === 'assistant'"
      class="assistant-card"
      :class="{ expanded, 'free-query-result': message.answerOrigin === 'template_free' }"
    >
      <div v-if="message.answerOrigin === 'template_free'" class="free-query-notice">
        未命中或未采用标准问题库 · 自由问数结果仅供参考。若本轮 SQL 由模型生成，须核对字段、筛选条件、单位和统计口径后才能用于正式快报。
      </div>
      <div v-if="docFiles.length || message.route" class="card-toolbar">
        <button
          v-for="f in docFiles"
          :key="f.url"
          class="mini-btn"
          title="在窗口中预览文档"
          @click="previewing = f"
        >
          <Icon name="file" :size="14" />
          预览{{ labelOf(f) }}
        </button>
        <a
          v-for="f in docFiles"
          :key="'dl-' + f.url"
          class="mini-btn"
          :href="f.url"
          :download="f.filename"
          title="下载文件"
        >
          <Icon name="download" :size="14" />
        </a>
        <span v-if="message.route" class="scene-badge" :class="message.route">
          {{ routeLabel(message.route) }}
        </span>
        <button
          class="mini-btn card-expand"
          :title="expanded ? '恢复为三分之二宽度' : '撑满消息区'"
          @click="expanded = !expanded"
        >
          <Icon :name="expanded ? 'shrink' : 'expand'" :size="14" />
        </button>
      </div>

      <div v-for="s in message.subagents || []" :key="s.id" class="subagent" :class="s.status">
        <details :open="s.status === 'running'">
          <summary>
            <span class="sa-state">
              {{ s.status === 'done' ? '✓' : s.status === 'error' ? '!' : '' }}
            </span>
            <span class="sa-prompt">{{ s.prompt }}</span>
          </summary>
          <div v-if="s.content" class="sa-content">{{ s.content }}</div>
        </details>
      </div>

      <div v-if="message.toolCalls?.length" class="tool-calls">
        <ToolCallCard v-for="c in message.toolCalls" :key="c.id" :call="c" />
      </div>

      <!-- 正文放在卡片之后：卡片负责展示数据，正文是卡片之上的结论式总结 -->
      <div class="assistant-body">
        <div v-if="!message.content && message.streaming" class="thinking">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
        </div>
        <template v-else>
          {{ message.content }}<span v-if="message.streaming" class="cursor" />
        </template>
      </div>
    </div>

    <div v-else-if="message.role === 'tool-standalone'" class="assistant-card">
      <div v-if="message.content" class="assistant-body">{{ message.content }}</div>
      <div v-if="message.artifacts?.length" class="tool-calls">
        <ArtifactView v-for="(a, i) in message.artifacts" :key="i" :artifact="a" />
      </div>
    </div>

    <Modal
      v-if="previewing"
      size="lg"
      :title="previewing.filename || '文档预览'"
      @close="previewing = null"
    >
      <DocxPreview :src="previewing.url" />
      <template #footer>
        <a class="btn btn-primary" :href="previewing.url" :download="previewing.filename">
          <Icon name="download" :size="15" />
          下载文件
        </a>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import ArtifactView from './ArtifactView.vue'
import DocxPreview from './DocxPreview.vue'
import Icon from './Icon.vue'
import Modal from './Modal.vue'
import ToolCallCard from './ToolCallCard.vue'
import { chat } from '../stores/chat'

const props = defineProps({
  message: { type: Object, required: true },
  canEdit: { type: Boolean, default: false },
})

const previewing = ref(null)
const editing = ref(false)
const expanded = ref(false)
const editDraft = ref('')
const copied = ref(false)
const editInput = ref(null)
let copyTimer = null

const ROUTE_LABELS = {
  sql: '土地变化查询',
  elder_care: '养老可达性分析',
  chat: '通用对话',
}

function routeLabel(route) {
  return ROUTE_LABELS[route] || route || '通用对话'
}

// 收集该轮回复生成的所有文件（挂在工具卡片或消息本体上）
const docFiles = computed(() => {
  const files = []
  for (const tc of props.message.toolCalls || []) {
    for (const a of tc.artifacts || []) {
      if (a.kind === 'file') files.push(fileInfo(a))
    }
  }
  for (const a of props.message.artifacts || []) {
    if (a.kind === 'file') files.push(fileInfo(a))
  }
  return files
})

function fileInfo(artifact) {
  const data = artifact.data || {}
  return {
    url: data.url || '',
    filename: data.filename || artifact.name || '文件',
  }
}

function labelOf(f) {
  const name = f.filename || ''
  if (name.includes('快报')) return '快报'
  return ''
}

async function copyText() {
  try {
    await navigator.clipboard.writeText(props.message.content || '')
    copied.value = true
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = false
    }, 1600)
  } catch {
    // 剪贴板权限被拒时静默忽略
  }
}

async function startEdit() {
  editing.value = true
  editDraft.value = props.message.content || ''
  await nextTick()
  const el = editInput.value
  if (el) {
    el.focus()
    const len = el.value.length
    el.setSelectionRange(len, len)
  }
}

function cancelEdit() {
  editing.value = false
  editDraft.value = ''
}

async function submitEdit() {
  const text = editDraft.value
  if (!text.trim()) return
  const ok = await chat.editAndResend(text)
  if (ok) editing.value = false
}
</script>
