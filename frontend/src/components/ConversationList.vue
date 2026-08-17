<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <span class="logo">GeoAgent</span>
      <button class="new-btn" title="新建会话" @click="onNew">＋</button>
    </div>
    <ul class="conv-list">
      <li
        v-for="c in chat.conversations"
        :key="c.id"
        :class="{ active: c.id === chat.currentId }"
        @click="onSelect(c.id)"
      >
        <div class="conv-title">{{ c.title }}</div>
        <div class="conv-meta">{{ c.model }} · {{ timeAgo(c.updated_at) }}</div>
      </li>
    </ul>
    <div class="sidebar-footer">Dev 模式 · 会话全局可见</div>
  </aside>
</template>

<script setup>
import { chat } from '../stores/chat'

async function onSelect(id) {
  try {
    await chat.selectConversation(id)
  } catch (err) {
    chat.error = err.message
  }
}

async function onNew() {
  try {
    await chat.createConversation()
  } catch (err) {
    chat.error = err.message
  }
}

function timeAgo(iso) {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}
</script>
