<template>
  <div class="tool-card" :class="call.status">
    <button class="tool-head" title="展开 / 收起详情" @click="open = !open">
      <Icon :name="iconName" class="tool-icon" :size="15" />
      <span class="tool-name" :title="call.name">{{ toolLabel(call.name) }}</span>
      <span class="tool-status" :class="call.status">{{ statusText }}</span>
      <Icon name="chevron-down" class="chev" :class="{ open }" :size="14" />
    </button>

    <div v-if="open" class="tool-detail">
      <template v-if="call.args && Object.keys(call.args).length">
        <span class="tool-args-label">参数</span>
        <pre>{{ JSON.stringify(call.args, null, 2) }}</pre>
      </template>
      <div
        v-if="call.result"
        class="tool-result"
        :class="{ error: call.status === 'error' }"
      >
        {{ call.result }}
      </div>
    </div>

    <div v-if="call.artifacts?.length" class="tool-artifacts">
      <ArtifactView v-for="(a, i) in call.artifacts" :key="i" :artifact="a" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import ArtifactView from './ArtifactView.vue'
import Icon from './Icon.vue'
import { toolLabel } from '../toolLabels'

const props = defineProps({
  call: { type: Object, required: true },
})

const open = ref(props.call.status !== 'done')

const statusText = computed(
  () =>
    ({ running: '正在执行', done: '完成', error: '失败' }[props.call.status] ||
      props.call.status),
)

// 按工具类型选择更贴合语义的图标，避免全用齿轮
const iconName = computed(() => {
  const name = props.call.name || ''
  if (name === 'generate_briefing') return 'file'
  if (name.includes('sql') || name.includes('list_tables') || name.includes('describe_table')) {
    return 'database'
  }
  if (
    name.includes('summary') ||
    name.includes('stat') ||
    name.includes('fragment') ||
    name.includes('conversion')
  ) {
    return 'chart'
  }
  return 'cog'
})
</script>
