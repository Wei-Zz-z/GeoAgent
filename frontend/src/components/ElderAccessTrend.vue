<template>
  <section class="access-trend" aria-label="不同时长的统计趋势">
    <div class="trend-head"><strong>随时间变化</strong><div role="group" aria-label="趋势指标"><button type="button" :aria-pressed="metric === 'area'" @click="metric = 'area'">面积</button><button type="button" :aria-pressed="metric === 'population'" @click="metric = 'population'">人口</button></div></div>
    <svg v-if="points.length" viewBox="0 0 250 106" role="group" :aria-label="metric === 'area' ? '概略覆盖面积趋势，单位亩' : '2020年总人口估算趋势，单位人'">
      <path d="M16 80 H234" stroke="#cbd5e1" fill="none" />
      <polygon :points="`16,80 ${points.map(point => `${point.x},${point.y}`).join(' ')} ${points.at(-1).x},80`" :fill="color" fill-opacity="0.08" />
      <polyline :points="points.map(point => `${point.x},${point.y}`).join(' ')" fill="none" :stroke="color" stroke-width="2" />
      <g v-for="point in points" :key="point.minutes" role="button" tabindex="0" :aria-label="`${point.minutes}分钟，${point.value}${metric === 'area' ? '亩' : '人'}`" :aria-pressed="point.minutes === minutes" @click="$emit('select-time', point.minutes)" @keydown.enter.prevent="$emit('select-time', point.minutes)" @keydown.space.prevent="$emit('select-time', point.minutes)">
        <rect :x="point.x - 16" y="0" width="32" height="106" fill="transparent" />
        <circle :cx="point.x" :cy="point.y" :r="point.minutes === minutes ? 5 : 3" :fill="point.minutes === minutes ? color : '#fff'" :stroke="color" stroke-width="2" />
        <text :x="point.x" y="100" text-anchor="middle" :fill="point.minutes === minutes ? color : '#64748b'">{{ point.minutes }}</text>
        <title>{{ point.minutes }}分钟：{{ point.value }}{{ metric === 'area' ? '亩' : '人' }}</title>
      </g>
    </svg>
    <p v-else>该指标暂未记录。</p>
    <small>{{ metric === 'area' ? '概略覆盖面积（亩）' : '2020年总人口估算（人）' }} · 点击时间点切换地图</small>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { areaMu } from '../elderAccessPresentation.js'
const props = defineProps({ rows: { type: Array, default: () => [] }, minutes: Number })
defineEmits(['select-time'])
const metric = ref('area')
const color = computed(() => metric.value === 'area' ? '#2563eb' : '#0f766e')
const points = computed(() => {
  const rows = [...props.rows].sort((a, b) => a.minutes - b.minutes).map(row => ({ minutes: row.minutes,
    value: metric.value === 'area' ? areaMu(row.coverage_area_m2, row.coverage_area_mu) : row.covered_population_2020 }))
  const valid = rows.filter(row => row.value != null && Number.isFinite(Number(row.value)))
  const max = Math.max(1, ...valid.map(row => Number(row.value)))
  return valid.map(row => ({ ...row, x: rows.length === 1 ? 125 : 16 + rows.indexOf(row) / (rows.length - 1) * 218, y: 80 - Number(row.value) / max * 66 }))
})
</script>

<style scoped>
.access-trend { padding-top: 12px; border-top: 1px solid #e2e8f0; }
.trend-head { display: flex; align-items: center; justify-content: space-between; font-size: 12px; }
button { cursor: pointer; padding: 4px 8px; background: white; color: #64748b; border: 1px solid #e2e8f0; }
button[aria-pressed="true"] { color: #1d4ed8; background: #eff6ff; border-color: #93c5fd; }
svg { display: block; width: 100%; margin: 6px 0; }
g { cursor: pointer; outline: none; } g:focus-visible circle { stroke: #0f172a; stroke-width: 4px; }
text { font-size: 10px; }
small, p { color: #64748b; font-size: 10px; line-height: 1.5; }
.access-trend { border-color: #e6ede9; }
.trend-head > strong { color: #34584e; }
.trend-head > div { display: flex; padding: 2px; background: #edf3f0; border-radius: 7px; }
button { border: 0; border-radius: 5px; background: transparent; font-size: 10px; }
button[aria-pressed="true"] { background: #fff; color: #215b51; box-shadow: 0 1px 4px #163e3b15; }
button:focus-visible { outline: 2px solid #147d6c; }
</style>
