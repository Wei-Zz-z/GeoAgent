<template>
  <CountyMapWorkspace v-if="countyMode" />
  <div v-else class="app">
    <ConversationList />
    <ChatWindow />
    <aside v-if="chat.currentId" class="template-panel" :class="{ 'template-panel-open': panelOpen }">
      <div class="template-panel-head">
        <button class="icon-btn" type="button" :title="panelOpen ? '收起模板工作区' : '展开模板工作区'" :aria-expanded="panelOpen" @click="panelOpen = !panelOpen">
          {{ panelOpen ? '›' : '‹' }}
        </button>
        <span v-if="panelOpen">模板与报告 · <a href="/?view=county-map">区县地图</a></span>
      </div>
      <TemplateWorkflow v-show="panelOpen" :key="chat.currentId" />
    </aside>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { onMounted } from 'vue'
import ConversationList from './components/ConversationList.vue'
import ChatWindow from './components/ChatWindow.vue'
import TemplateWorkflow from './components/TemplateWorkflow.vue'
import CountyMapWorkspace from './components/CountyMapWorkspace.vue'
import { chat } from './stores/chat'

const panelOpen = ref(window.innerWidth > 1100)
const countyMode = new URLSearchParams(window.location.search).get('view') === 'county-map'

onMounted(() => {
  if (!countyMode) chat.init().catch((err) => {
    chat.error = `初始化失败：${err.message}`
  })
})
</script>
