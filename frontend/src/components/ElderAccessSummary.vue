<template>
  <aside class="access-summary" aria-label="当前时间档统计" aria-live="polite">
    <div class="summary-heading"><span>范围概览</span><strong>{{ minutes }}<small> 分钟 · {{ travelLabel }}</small></strong></div>
    <div class="coverage-card">
      <span>概略覆盖面积</span><strong>{{ areaMu(stats?.coverage_area_m2, stats?.coverage_area_mu) }}<small> 亩</small></strong>

    </div>
    <div class="summary-pair">
      <div><span>可达道路合计</span><strong>{{ stats?.road_length_m ?? '未记录' }}<small> 米</small></strong></div>
      <div><span>面内其他机构</span><strong>{{ stats?.other_facility_count ?? '未记录' }}<small> 个</small></strong></div>
    </div>
    <ElderAccessTrend :rows="data.statistics" :minutes="minutes" @select-time="$emit('select-time', $event)" />
    <section v-if="data.population_reference" class="summary-section">
      <h4>2020年总人口估算 <small>非老年人口</small></h4>
      <template v-if="data.population_reference.available">
        <strong class="population-number">{{ stats?.covered_population_2020 ?? '未记录' }}<small> 人在概略面内</small></strong>
        <div class="population-track" aria-hidden="true"><i :style="{ width: `${Math.max(0, Math.min(100, stats?.population_coverage_percent ?? 0))}%` }"></i></div>
        <p>占研究区估算人口 {{ stats?.population_coverage_percent == null ? '未计算' : stats.population_coverage_percent + '%' }}</p>
      </template><p v-else>人口数据不可用，未计算。</p>
    </section>
    <section v-if="data.village_reference" class="summary-section">
      <h4>相交历史边界 <strong>{{ stats?.historical_village_unit_count ?? '无数据' }}<small> 个单元</small></strong></h4>
      <p>其中原名含“社区” {{ stats?.historical_community_named_count ?? '无数据' }} 个</p>
      <p v-if="boundaryCodes.length" class="boundary-hint">点击下列名称，定位并高亮边界</p>
      <p v-else-if="stats?.historical_village_units?.length" class="boundary-hint">重新分析后可点击名称定位边界</p>
      <ul v-if="stats?.historical_village_units?.length" class="boundary-list">
        <li v-for="item in stats.historical_village_units.slice(0, 4)" :key="item.code"><button type="button" :disabled="!boundaryCodes.includes(item.code)" :aria-pressed="selectedBoundary === item.code" :title="boundaryCodes.includes(item.code) ? '点击高亮边界，再次点击取消' : '该历史记录无边界图形，重新分析后可定位'" @click="$emit('select-boundary', item.code)"><span>{{ item.name }}<small>{{ item.kind }}</small></span><strong>{{ areaMu(item.intersection_m2) }}<small> 亩</small></strong></button></li>
      </ul>
      <p v-if="stats?.historical_village_units?.length > 4">其余 {{ stats.historical_village_units.length - 4 }} 个见下方明细。</p>

    </section>
    <section v-if="data.district_reference?.available" class="summary-section study-section">
      <h4>{{ data.district_reference.name }}</h4>
      <p>区内概略覆盖 {{ areaMu(stats?.district_intersection_m2) }} 亩</p>
      <p>占研究区面积 {{ stats?.district_coverage_percent ?? '未记录' }}%</p>
    </section>
  </aside>
</template>

<script setup>
import ElderAccessTrend from './ElderAccessTrend.vue'
import { areaMu } from '../elderAccessPresentation.js'
defineProps({ data: { type: Object, required: true }, stats: Object, minutes: Number, travelLabel: String, selectedBoundary: String, boundaryCodes: { type: Array, default: () => [] } })
defineEmits(['select-time', 'select-boundary'])
</script>

<style scoped>
.access-summary { display: flex; flex-direction: column; gap: 12px; min-width: 0; padding: 16px; max-height: 730px; overflow-y: auto; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; color: #334155; }
.access-summary > * { flex-shrink: 0; }
.summary-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; }
.summary-heading strong { font-size: 24px; color: #0f172a; }
small { font-size: 11px; font-weight: 400; }
.coverage-card { padding: 16px; border: 1px solid #bfdbfe; border-radius: 10px; background: #eff6ff; }
.coverage-card > span, .coverage-card > strong, .coverage-card > small { display: block; }
.coverage-card > span { font-size: 12px; }
.coverage-card > strong { margin: 6px 0; color: #1d4ed8; font-size: 29px; font-variant-numeric: tabular-nums; }
.coverage-card > small { color: #64748b; line-height: 1.5; }
.summary-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.summary-pair > div { padding: 10px; background: white; border: 1px solid #e2e8f0; border-radius: 8px; }
.summary-pair span, .summary-pair strong { display: block; }
.summary-pair span { margin-bottom: 6px; font-size: 11px; color: #64748b; }
.summary-pair strong { font-size: 16px; }
.summary-section { padding-top: 12px; border-top: 1px solid #e2e8f0; }
h4 { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px; font-size: 12px; margin: 0 0 8px; }
p { margin: 5px 0 0; font-size: 11px; line-height: 1.6; }
.population-number { color: #0f766e; font-size: 23px; }
.population-track { margin-top: 10px; height: 6px; border-radius: 4px; overflow: hidden; background: #e2e8f0; }
.population-track i { display: block; height: 100%; min-width: 0; background: #0d9488; }
.boundary-list { padding: 0; margin: 8px 0; list-style: none; }
.boundary-list li { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 2px 0; border-bottom: 1px solid #e2e8f0; font-size: 12px; }
.boundary-list span small { display: block; margin-top: 2px; color: #64748b; }
.boundary-list strong { white-space: nowrap; font-size: 12px; }
.boundary-list button { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; padding: 6px; text-align: left; color: inherit; background: transparent; border: 1px solid transparent; border-radius: 6px; cursor: pointer; }
.boundary-list button[aria-pressed="true"] { border-color: #a78bfa; background: #f5f3ff; }
.boundary-list button:disabled { cursor: default; }
.boundary-hint { color: #6d28d9; }
.access-summary { background: #fff; border-color: #dce7e4; border-radius: 12px; gap: 14px; scrollbar-width: thin; scrollbar-color: #bed2cc transparent; font-variant-numeric: tabular-nums; }
.summary-heading > span { color: #56736b; font-weight: 600; letter-spacing: 1px; }
.summary-heading strong { font-size: 25px; color: #163e3b; }
.coverage-card { position: relative; overflow: hidden; padding: 18px 16px; border: 0; border-radius: 12px; background: #163e3b; color: #c4e4da; }
.coverage-card::after { content: ''; position: absolute; width: 90px; height: 90px; border: 18px solid #ffffff08; border-radius: 50%; right: -35px; top: -30px; pointer-events: none; }
.coverage-card > strong { color: #fff; font-size: 30px; letter-spacing: -.6px; margin: 9px 0 0; }
.coverage-card > strong small { color: #b8d8ce; margin-left: 3px; font-size: 12px; }
.summary-pair > div { background: #f4f8f6; border: 0; border-radius: 9px; padding: 12px 10px; }
.summary-pair strong { color: #215b51; font-size: 18px; }
.summary-section { border-color: #e6ede9; padding-top: 14px; }
.population-number { font-size: 26px; }
.population-track { height: 5px; background: #edf2ef; }
.population-track i { background: #188c76; border-radius: 5px; }
.boundary-list li { border: 0; padding: 3px 0; }
.boundary-list button { padding: 10px 8px; border-color: #e5ece9; border-radius: 8px; transition: background .15s, border-color .15s; }
.boundary-list button:not(:disabled):hover { background: #f5f3ff; border-color: #c4b5fd; }
.boundary-list button[aria-pressed="true"] { box-shadow: inset 3px 0 #7c3aed; }
.boundary-list button:focus-visible { outline: 2px solid #7c3aed; outline-offset: 2px; }
.boundary-hint { font-size: 10px; }
.study-section { background: #f4f8f6; padding: 12px; border-radius: 9px; border: 0; }

</style>
