<template>
  <section class="monitoring-section" aria-label="监测指标">
    <h4>监测指标 <small>居住用地面积口径</small></h4>
    <div class="indicator-heading"><span>{{ indicators.official.name }}</span><strong>{{ indicators.official.display }}</strong></div>
    <p>{{ indicators.official.reason }}</p>
    <p v-if="indicators.trial.value != null" class="trial-value">本设施15分钟试算 <strong>{{ indicators.trial.display }}</strong></p>
    <p v-if="data.travel_mode === 'driving'" class="fixed-time">当前为驾车，不计算步行15分钟指标。</p>
    <p v-else-if="minutes !== 15" class="fixed-time">指标固定采用15分钟步行结果；当前地图为{{ minutes }}分钟。</p>
    <details>
      <summary>查看公式与数据缺项</summary>
      <p>{{ indicators.official.formula || '覆盖的居住用地面积 ÷ 研究区居住用地总面积 × 100%' }}</p>
      <p>正式区域指标需合并全部适用社区养老设施的步行范围并去重；本设施试算不能替代。</p>
      <p>{{ indicators.trial.name }}：{{ indicators.trial.display }}。{{ indicators.trial.reason }}</p>
      <p v-if="indicators.trial.value != null">本设施试算依据概略覆盖面，仍需核实实际通行范围。</p>
      <p>行政区面积占比、人口估算及相交边界数保留为辅助统计。</p>
      <p v-if="indicators.official.required_data?.length">所需数据：{{ indicators.official.required_data.join('、') }}</p>
      <p v-if="indicators.official.source_section" class="source">依据：{{ indicators.official.source }} {{ indicators.official.source_section }}</p>
    </details>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { elderMonitoringIndicators } from '../elderMonitoringIndicators.js'

const props = defineProps({ data: { type: Object, required: true }, minutes: Number })
const indicators = computed(() => elderMonitoringIndicators(props.data))
</script>

<style scoped>
.monitoring-section { padding-top: 14px; border-top: 1px solid #e6ede9; font-size: 12px; }
h4 { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px; margin: 0 0 10px; font-size: 12px; }
small, .source, .fixed-time { font-size: 10px; color: #64748b; font-weight: normal; }
.indicator-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; line-height: 1.6; }
.indicator-heading > strong { flex-shrink: 0; color: #215b51; }
p { margin: 6px 0 0; font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.trial-value { display: flex; justify-content: space-between; gap: 8px; }
.trial-value strong { color: #215b51; }
details { margin-top: 10px; }
summary { cursor: pointer; color: #46655c; font-size: 11px; }
</style>
