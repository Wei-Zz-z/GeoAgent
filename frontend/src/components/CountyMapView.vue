<template>
  <section class="county-map">
    <h2>区县地类面积统计</h2>
    <p>模拟变化统计 · 单位：亩 · {{ data.metadata.geometry_source }}</p>
    <p>{{ data.metadata.anchor_method }}。面积来自变化图斑属性，不是行政区总面积。</p>
    <label>地图上的图表：
      <select v-model="chartType"><option value="pie">面积占比饼图</option><option value="bar">面积柱状图</option></select>
    </label>
    <p>{{ data.metadata.feature_count }} 个合并图斑，{{ data.metadata.county_count }} 个区县。点击图表查看明细；图表密集时缩放地图，重叠图表会暂时隐藏。</p>
    <div ref="mapEl" class="county-canvas"></div>
    <p v-for="warning in data.metadata.warnings" :key="warning">⚠ {{ warning }}</p>
    <p v-if="!selected">点击一个区县图表查看地类、颜色与完整数值。饼图展示前5类及其余地类合计，柱状图同口径。</p>
    <div v-else>
      <h3>{{ selected.name }}（{{ selected.XZQDM }}）</h3>
      <p>{{ selected.count }} 个合并图斑，总面积 {{ selected.area_mu.toFixed(2) }} 亩</p>
      <table><thead><tr><th>变化后地类</th><th>面积（亩）</th><th>占比</th></tr></thead>
        <tbody><tr v-for="[label, value] in Object.entries(selected.values).sort((a,b)=>b[1]-a[1])" :key="label">
          <td><span :style="{ background: countyColor(label, labels) }" class="county-key"></span>{{ label }}</td>
          <td>{{ value.toFixed(2) }}</td><td>{{ selected.area_mu ? (value / selected.area_mu * 100).toFixed(2) : '0.00' }}%</td>
        </tr></tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import Map from 'ol/Map'
import View from 'ol/View'
import GeoJSON from 'ol/format/GeoJSON'
import Feature from 'ol/Feature'
import Point from 'ol/geom/Point'
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import { fromLonLat } from 'ol/proj'
import { Style, Fill, Stroke, Icon, Text } from 'ol/style'
import 'ol/ol.css'
import { countySeries, countyColor } from '../countyChartUtils'

const props = defineProps({ data: { type: Object, required: true } })
const mapEl = ref(null), chartType = ref('pie'), selected = ref(null)
const labels = computed(() => [...new Set(props.data.features.flatMap(f => Object.keys(f.properties.values)))].sort())
let map, chartLayer

function chartStyle(feature) {
  const county = feature.get('county'), series = countySeries(county.values)
  const canvas = document.createElement('canvas')
  canvas.width = 100; canvas.height = 100
  const ctx = canvas.getContext('2d'), total = series.reduce((sum, item) => sum + item[1], 0)
  ctx.fillStyle = 'rgba(255,255,255,0.9)'; ctx.fillRect(3, 3, 94, 94)
  if (chartType.value === 'pie') {
    let angle = -Math.PI / 2
    series.forEach(([label, value]) => {
      const end = angle + (total ? value / total * Math.PI * 2 : 0)
      ctx.beginPath(); ctx.moveTo(50,50); ctx.arc(50,50,37,angle,end); ctx.closePath()
      ctx.fillStyle = countyColor(label, labels.value); ctx.fill()
      ctx.strokeStyle = 'white'; ctx.stroke(); angle = end
    })
  } else {
    const max = Math.max(1, ...series.map(item => item[1]))
    series.forEach(([label,value], i) => {
      ctx.fillStyle = countyColor(label, labels.value)
      ctx.fillRect(10 + i * 14, 85 - value / max * 70, 11, value / max * 70)
    })
    ctx.strokeStyle = '#374151'; ctx.beginPath(); ctx.moveTo(8,10); ctx.lineTo(8,86); ctx.lineTo(94,86); ctx.stroke()
  }
  return new Style({ image: new Icon({ img: canvas, scale: 0.65 }),
    text: new Text({ text: county.name, offsetY: 42, font: '12px sans-serif',
      fill: new Fill({ color: '#172554' }), stroke: new Stroke({ color: '#fff', width: 3 }) }) })
}

onMounted(() => {
  const polygons = new GeoJSON().readFeatures(props.data, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' })
  const source = new VectorSource({ features: polygons })
  const anchors = props.data.features.filter(f => f.properties.has_statistics !== false).map(f => new Feature({ geometry: new Point(fromLonLat(f.properties.anchor)), county: f.properties }))
  const cache = new globalThis.Map()
  chartLayer = new VectorLayer({ source: new VectorSource({ features: anchors }), declutter: true,
    style: feature => { if (!cache.has(feature)) cache.set(feature, chartStyle(feature)); return cache.get(feature) } })
  watch(chartType, () => { cache.clear(); chartLayer.changed() })
  map = new Map({ target: mapEl.value, layers: [new VectorLayer({ source,
    style: new Style({ fill: new Fill({ color: '#e2e8f0' }), stroke: new Stroke({ color: '#94a3b8', width: 1 }) }) }), chartLayer],
    view: new View({ center: fromLonLat([120,29]), zoom: 7 }) })
  map.getView().fit(source.getExtent(), { padding: [70,70,70,70], maxZoom: 11 })
  map.on('singleclick', event => {
    const feature = map.forEachFeatureAtPixel(event.pixel, f => f, { layerFilter: layer => layer === chartLayer })
    selected.value = feature?.get('county') || null
  })
})
onBeforeUnmount(() => map?.setTarget(undefined))
</script>

<style scoped>
.county-map { padding: 20px; max-width: 1400px; margin: auto; }
.county-map p { margin: 12px 0; color: #475569; }
.county-canvas { height: 620px; background: #f8fafc; border: 1px solid #cbd5e1; }
table { border-collapse: collapse; width: 100%; max-width: 700px; }
th, td { border: 1px solid #cbd5e1; padding: 8px; text-align: right; }
th:first-child, td:first-child { text-align: left; }
.county-key { display:inline-block; width:12px; height:12px; margin-right:8px; }
select { padding: 6px; }
</style>
