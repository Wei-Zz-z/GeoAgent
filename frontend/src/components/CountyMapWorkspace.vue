<template>
  <main style="width:100%; overflow:auto; padding:16px">
    <a href="/">返回模板与问数</a>
    <h1>区县地图统计</h1>
    <p>变化图斑用于统计；另选行政区边界可显示完整区县范围。两份数据按区划代码关联，不调用旧数据库或模型。</p>
    <p><label>变化图斑ZIP：<input type="file" accept=".zip" :disabled="busy" @change="vectorFile = $event.target.files[0]" /></label></p>
    <p><label>行政区边界ZIP（可选）：<input type="file" accept=".zip" :disabled="busy" @change="boundaryFile = $event.target.files[0]" /></label></p>
    <button :disabled="busy || !vectorFile" @click="upload">生成区县地图</button>
    <p v-if="busy">正在汇总图斑并核对区县定位，请稍候……</p>
    <p v-if="error" role="alert" style="color:#b91c1c">{{ error }}</p>
    <CountyMapView v-if="artifact" :key="generation" :data="artifact.data" />
  </main>
</template>
<script setup>
import { ref } from 'vue'
import CountyMapView from './CountyMapView.vue'
const artifact = ref(null), busy = ref(false), error = ref(''), generation = ref(0)
const vectorFile = ref(null), boundaryFile = ref(null)
async function upload() {
  if (!vectorFile.value) return
  if ([vectorFile.value, boundaryFile.value].some(file => file?.size > 100 * 1024 * 1024)) { error.value = '每份ZIP不得超过100MB。'; return }
  busy.value = true; error.value = ''; artifact.value = null
  try {
    const body = new FormData()
    body.append('vector', vectorFile.value)
    if (boundaryFile.value) body.append('boundary', boundaryFile.value)
    const response = await fetch('/api/county-charts/build', { method: 'POST', body })
    const result = await response.json()
    if (!response.ok) throw new Error(result.detail || '生成失败')
    artifact.value = result; generation.value++
  } catch (err) { error.value = err.message }
  finally { busy.value = false }
}
</script>
