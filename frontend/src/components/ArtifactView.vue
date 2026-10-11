<template>
  <div class="artifact">
    <!-- 地图结果 -->
    <div v-if="artifact.kind === 'geojson'" class="map-card">
      <div class="artifact-head">
        <Icon name="map-pin" :size="14" />
        <span>{{ artifact.name || '空间分布图' }}</span>
      </div>
      <div ref="mapEl" class="mini-map"></div>
    </div>

    <!-- 表格结果 -->
    <div v-else-if="artifact.kind === 'table'" class="table-card">
      <div class="artifact-head">
        <Icon name="chart" :size="14" />
        <span>{{ artifact.name || '查询结果' }}</span>
        <span style="margin-left: auto; font-size: 12px; color: var(--text-3)">
          {{ rows.length }} 行
        </span>
        <button class="icon-btn" title="复制为 CSV" @click="copyTable">
          <Icon :name="copied ? 'check' : 'copy'" :size="14" />
        </button>
      </div>
      <div class="table-scroll">
        <table class="artifact-table">
          <thead>
            <tr>
              <th v-for="c in columns" :key="c">{{ c }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in rows" :key="i">
              <td v-for="(c, j) in columns" :key="c">{{ cellValue(row, c, j) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 文件结果 -->
    <div v-else-if="artifact.kind === 'file'" class="file-chip" :title="filename">
      <Icon name="file" class="file-icon" :size="15" />
      <span class="file-name">{{ filename }}</span>
      <a
        class="icon-link"
        :href="fileUrl"
        :download="filename"
        title="下载文件"
        @click.stop
      >
        <Icon name="download" :size="14" />
      </a>
    </div>

    <!-- 图表结果（饼图 / 柱状图 / 折线图） -->
    <ChartView v-else-if="artifact.kind === 'chart'" :artifact="artifact" />

    <!-- 其他类型：JSON 预览 -->
    <pre v-else class="artifact-json">{{ JSON.stringify(artifact.data, null, 2) }}</pre>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import GeoJSON from 'ol/format/GeoJSON'
import Map from 'ol/Map'
import TileLayer from 'ol/layer/Tile'
import VectorLayer from 'ol/layer/Vector'
import { fromLonLat } from 'ol/proj'
import OSM from 'ol/source/OSM'
import VectorSource from 'ol/source/Vector'
import { Fill, Stroke, Style } from 'ol/style'
import View from 'ol/View'
import ChartView from './ChartView.vue'
import Icon from './Icon.vue'

const props = defineProps({
  artifact: { type: Object, required: true },
})

const mapEl = ref(null)
const copied = ref(false)
let map = null
let copyTimer = null

const columns = computed(() => props.artifact.data?.columns || [])
const rows = computed(() => props.artifact.data?.rows || [])
const filename = computed(() => props.artifact.data?.filename || props.artifact.name || '文件')
const fileUrl = computed(() => props.artifact.data?.url || '')

// 兼容两种行格式：对象行按列名取值，数组行按下标取值
function cellValue(row, column, index) {
  if (Array.isArray(row)) return row[index]
  return row[column]
}

async function copyTable() {
  const esc = (v) => {
    const s = String(v ?? '')
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const csv = [
    columns.value.map(esc).join(','),
    ...rows.value.map((r) => columns.value.map((c, j) => esc(cellValue(r, c, j))).join(',')),
  ].join('\n')
  try {
    await navigator.clipboard.writeText(csv)
    copied.value = true
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = false
    }, 1600)
  } catch {
    // 剪贴板权限被拒时静默忽略
  }
}

onMounted(() => {
  if (props.artifact.kind !== 'geojson' || !mapEl.value) return
  const features = new GeoJSON().readFeatures(props.artifact.data, {
    dataProjection: 'EPSG:4326',
    featureProjection: 'EPSG:3857',
  })
  if (!features.length) return

  const source = new VectorSource({ features })
  // 图层样式可随 GeoJSON 一起下发（预警图斑为红色描边 + 半透明红填充）
  const styleSpec = props.artifact.data?.style
  const vectorLayer = new VectorLayer({ source })
  if (styleSpec) {
    vectorLayer.setStyle(
      new Style({
        stroke: new Stroke({
          color: styleSpec.stroke || '#d93025',
          width: styleSpec.strokeWidth || 2,
        }),
        fill: new Fill({ color: styleSpec.fill || 'rgba(217, 48, 37, 0.28)' }),
      }),
    )
  }
  map = new Map({
    target: mapEl.value,
    layers: [new TileLayer({ source: new OSM() }), vectorLayer],
    view: new View({ center: fromLonLat([116.4, 39.9]), zoom: 10 }),
    controls: [],
  })
  map.getView().fit(source.getExtent(), { padding: [24, 24, 24, 24], maxZoom: 17 })
})

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
  if (map) {
    map.setTarget(undefined)
    map = null
  }
})
</script>
