<template>
  <details class="access-notes">
    <summary>试算说明与数据来源 <span>概略范围 · 坐标待核实</span></summary>
    <ul>
      <li>彩色面由可达道路形成，尚未剔除水体、围墙和封闭用地，不代表面内每个位置均可到达。设施入口采用直线接入假设。</li>
      <li>人口为2020年总人口栅格估算，不是老年人口、现状人口或实际服务人数。</li>
      <li>历史边界混有村、社区和街道等单元；相交不代表整单元覆盖，不计算现行社区覆盖率。</li>
      <li>灰色面只扣除本次单设施概略面，不代表所有养老设施的真实服务盲区；面内其他机构数仅为几何叠加。</li>
      <li v-if="data.travel_mode === 'driving'">驾车为简化试算，未计拥堵、信号灯、转向禁令及逐路限速。</li>
    </ul>
    <details v-for="item in sources" :key="item.label"><summary>{{ item.label }}</summary><p v-for="text in item.text" :key="text">{{ text }}</p></details>
  </details>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ data: { type: Object, required: true } })
const sources = computed(() => [
  ['路网与计算假设', { notice: props.data.notice }],
  ['人口数据', props.data.population_reference], ['历史村级边界', props.data.village_reference],
  ['历史乡镇边界', props.data.township_reference], ['研究区边界', props.data.district_reference],
  ['社区小样本', props.data.community_reference],
].filter(([, value]) => value).map(([label, value]) => ({ label,
  text: [...new Set([value.source, value.version_status, value.notice].filter(Boolean))] })))
</script>

<style scoped>
.access-notes { margin: 8px 12px 12px; border: 1px solid #f0d9ad; border-radius: 8px; background: #fffbf2; padding: 10px 12px; font-size: 12px; color: #475569; }
summary { cursor: pointer; font-weight: 600; } summary span { margin-left: 8px; font-size: 11px; font-weight: normal; color: #926014; }
ul { padding-left: 18px; line-height: 1.7; } p { line-height: 1.6; margin: 6px 0; }
details details { padding: 7px 0; border-top: 1px solid #eee4d2; }
</style>
