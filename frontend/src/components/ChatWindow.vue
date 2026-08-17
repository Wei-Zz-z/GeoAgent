<template>
  <main class="chat">
    <header class="chat-header">
      <div class="chat-title">{{ currentTitle }}</div>
      <select
        class="model-select"
        :value="chat.currentModel"
        title="切换会话模型"
        @change="onModelChange"
      >
        <option v-for="m in chat.models" :key="m.id" :value="m.id" :disabled="!m.available">
          {{ m.id }}{{ m.available ? '' : '（未配置 Key）' }}
        </option>
      </select>
    </header>

    <div v-if="chat.error" class="error-banner">{{ chat.error }}</div>

    <div ref="messagesEl" class="messages">
      <div v-if="!chat.messages.length" class="empty-hint">
        输入一句自然语言开始地理分析，例如「给国贸做一个 10 公里的缓冲区」
      </div>
      <MessageBubble v-for="m in chat.messages" :key="m.id" :message="m" />
    </div>

    <footer class="composer">
      <textarea
        v-model="draft"
        rows="2"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        :disabled="chat.streaming"
        @keydown.enter.exact.prevent="onSend"
      />
      <button class="send-btn" :disabled="chat.streaming || !draft.trim()" @click="onSend">
        {{ chat.streaming ? '分析中…' : '发送' }}
      </button>
    </footer>
  </main>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import { chat } from '../stores/chat'

const draft = ref('')
const messagesEl = ref(null)

const currentTitle = computed(
  () => chat.conversations.find((c) => c.id === chat.currentId)?.title || '会话',
)

// 消息条数或最后一条内容变化时（流式 token 逐字追加）自动滚动到底部
watch(
  () => [chat.messages.length, chat.messages[chat.messages.length - 1]?.content],
  async () => {
    await nextTick()
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  },
)

function onSend() {
  if (chat.sendMessage(draft.value)) draft.value = ''
}

async function onModelChange(event) {
  try {
    await chat.switchModel(event.target.value)
  } catch (err) {
    chat.error = err.message
  }
}
</script>
