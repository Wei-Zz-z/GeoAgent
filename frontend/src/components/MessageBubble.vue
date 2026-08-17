<template>
  <div class="msg" :class="message.role">
    <div v-if="message.role === 'user'" class="bubble user-bubble">{{ message.content }}</div>

    <template v-else-if="message.role === 'assistant'">
      <div v-if="message.route" class="msg-route">
        → {{ message.route === 'geo' ? '地理分析智能体' : '通用对话智能体' }}
      </div>
      <div class="bubble assistant-bubble">
        <div class="msg-content">
          {{ message.content }}<span v-if="message.streaming" class="cursor" />
        </div>
      </div>
      <ToolCallCard v-for="c in message.toolCalls" :key="c.id" :call="c" />
    </template>

    <div v-else-if="message.role === 'tool-standalone'" class="tool-standalone">
      <div class="tool-result-text">{{ message.content }}</div>
      <ArtifactView v-for="(a, i) in message.artifacts" :key="i" :artifact="a" />
    </div>
  </div>
</template>

<script setup>
import ToolCallCard from './ToolCallCard.vue'
import ArtifactView from './ArtifactView.vue'

defineProps({
  message: { type: Object, required: true },
})
</script>
