<template>
  <div class="tool-card" :class="call.status">
    <div class="tool-head">
      <span class="tool-icon" :class="{ pulse: call.status === 'running' }">⚙</span>
      <span class="tool-name">{{ call.name }}</span>
      <span class="tool-status" :class="call.status">{{ statusText }}</span>
      <details class="tool-args">
        <summary>参数</summary>
        <pre>{{ JSON.stringify(call.args, null, 2) }}</pre>
      </details>
    </div>
    <div v-if="call.result" class="tool-result-text">{{ call.result }}</div>
    <ArtifactView v-for="(a, i) in call.artifacts" :key="i" :artifact="a" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ArtifactView from './ArtifactView.vue'

const props = defineProps({
  call: { type: Object, required: true },
})

const statusText = computed(
  () => ({ running: '正在调用…', done: '完成', error: '失败' }[props.call.status] || props.call.status),
)
</script>
