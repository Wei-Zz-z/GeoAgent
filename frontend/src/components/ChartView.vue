<template>
  <div class="chart-card">
    <div class="artifact-head">
      <Icon name="chart" :size="14" />
      <span>{{ chartTitle }}</span>
    </div>

    <PieChart v-if="kind === 'pie'" :labels="labels" :series="series" />
    <BarChart v-else-if="kind === 'bar'" :labels="labels" :series="series" />
    <LineChart v-else-if="kind === 'line'" :labels="labels" :series="series" />
    <pre v-else class="artifact-json">{{ JSON.stringify(data, null, 2) }}</pre>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import BarChart from './charts/BarChart.vue'
import LineChart from './charts/LineChart.vue'
import PieChart from './charts/PieChart.vue'
import Icon from './Icon.vue'

import { artifactLabel } from '../artifactLabels'

const props = defineProps({
  artifact: { type: Object, required: true },
})

const data = computed(() => props.artifact.data || {})
const kind = computed(() =>
  ['pie', 'bar', 'line'].includes(data.value.chart_type) ? data.value.chart_type : '',
)
const chartTitle = computed(() => data.value.title || artifactLabel(props.artifact.name, 'chart'))
const labels = computed(() =>
  Array.isArray(data.value.labels) ? data.value.labels.map(String) : [],
)
const series = computed(() =>
  (Array.isArray(data.value.series) ? data.value.series : [])
    .map((s) => ({
      name: String(s?.name ?? ''),
      values: Array.isArray(s?.values) ? s.values.map((v) => Number(v)) : [],
    }))
    .filter((s) => s.values.length),
)
</script>
