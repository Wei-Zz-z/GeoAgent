<template>
  <LoginGate v-if="!auth.ready" />
  <CountyMapWorkspace v-else-if="countyMode" />
  <OverlayWorkspace v-else-if="overlayMode" />
  <div v-else class="app">
    <ConversationList />
    <ChatWindow />
    <aside v-if="chat.currentId" class="template-panel" :class="{ 'template-panel-open': panelOpen }">
      <div class="template-panel-head">
        <button class="icon-btn" type="button" :title="panelOpen ? '收起模板工作区' : '展开模板工作区'" :aria-expanded="panelOpen" @click="panelOpen = !panelOpen">
          {{ panelOpen ? '›' : '‹' }}
        </button>
        <span v-if="panelOpen">模板与报告</span>
      </div>
      <TemplateWorkflow v-show="panelOpen" :key="chat.currentId" />
    </aside>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { onMounted } from 'vue'
import ConversationList from './components/ConversationList.vue'
import LoginGate from './components/LoginGate.vue'
import ChatWindow from './components/ChatWindow.vue'
import TemplateWorkflow from './components/TemplateWorkflow.vue'
import CountyMapWorkspace from './components/CountyMapWorkspace.vue'
import OverlayWorkspace from './components/OverlayWorkspace.vue'
import { auth } from './auth'
import { chat } from './stores/chat'

const panelOpen = ref(window.innerWidth > 1100)
const countyMode = new URLSearchParams(window.location.search).get('view') === 'county-map'
const overlayMode = new URLSearchParams(window.location.search).get('view') === 'overlay'

onMounted(() => {
  // 先过入口验证（省域平台 ticket），确认登录态后再初始化会话
  auth
    .ensure()
    .then(() => {
      if (countyMode || overlayMode) return
      return chat.init().catch((err) => {
        chat.error = `初始化失败：${err.message}`
      })
    })
    .catch(() => {
      // 入口验证失败：LoginGate 已经展示原因，这里不重复提示
    })
})
</script>
