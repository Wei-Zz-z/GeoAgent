<template>
  <div class="app">
    <ConversationList />
    <ChatWindow />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import ConversationList from './components/ConversationList.vue'
import ChatWindow from './components/ChatWindow.vue'
import { chat } from './stores/chat'

onMounted(() => {
  chat.init().catch((err) => {
    chat.error = `初始化失败：${err.message}`
  })
})
</script>
