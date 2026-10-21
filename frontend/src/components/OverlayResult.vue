<template>
  <section class="overlay-result">
    <h2>套合完成</h2>
    <table class="overlay-summary">
      <tbody>
        <tr><th>套合表</th><td>{{ data.table }}</td></tr>
        <tr><th>期次</th><td>{{ data.label }}（{{ data.dataset_id }}）{{ data.registered ? '· 已登记' : '· 未登记' }}</td></tr>
        <tr><th>套合行数</th><td>{{ data.rows }} 行 / {{ data.counties }} 个县</td></tr>
        <tr><th>面积合计</th><td>{{ data.area_mu }} 亩（{{ data.area_m2 }} 平方米）</td></tr>
        <tr><th>涉及永农</th><td>{{ data.sfyn_yes }} 行</td></tr>
        <tr><th>涉及红线</th><td>{{ data.sfhx_yes }} 行</td></tr>
        <tr><th>耗时</th><td>{{ data.elapsed_s }} 秒</td></tr>
      </tbody>
    </table>

    <p v-if="data.registered" class="overlay-note">
      已登记为 “{{ data.label }}”，可在对话页的“数据表”下拉框中选择该期。
    </p>

    <h3>输入图层</h3>
    <table class="overlay-summary">
      <thead><tr><th>图层</th><th>要素数</th><th>来源</th><th>编码</th><th>坐标系</th></tr></thead>
      <tbody>
        <tr v-for="(info, role) in data.layers" :key="role">
          <td>{{ info.label }}</td>
          <td>{{ info.rows }}</td>
          <td>{{ info.source }}</td>
          <td>{{ info.encoding }}</td>
          <td>EPSG:{{ info.epsg }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="!hasYjn" class="overlay-note">本次未上传永久基本农田图层，“是否涉及永农”统一记为“否”。</p>
    <p v-if="!hasHx" class="overlay-note">本次未上传生态保护红线图层，“是否涉及红线”统一记为“否”。</p>

    <div v-if="data.warnings && data.warnings.length" class="overlay-warnings">
      <h3>提示</h3>
      <ul><li v-for="(item, index) in data.warnings" :key="index">{{ item }}</li></ul>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
const hasYjn = computed(() => Boolean(props.data.layers && props.data.layers.yjn))
const hasHx = computed(() => Boolean(props.data.layers && props.data.layers.hx))
</script>