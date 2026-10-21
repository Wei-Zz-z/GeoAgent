<template>
  <div class="artifact">
    <!-- 地图结果（可内嵌关联明细表：点表格行定位并高亮对应图斑） -->
    <div v-if="artifact.kind === 'geojson'" class="map-card">
      <div class="artifact-head">
        <Icon name="map-pin" :size="14" />
        <span>{{ artifactLabel(artifact.name, artifact.kind) }}</span>
        <span v-if="linkedRows.length" class="artifact-hint">点击表格行定位并高亮图斑</span>
      </div>
      <div ref="mapEl" class="mini-map"></div>
      <div v-if="linkedRows.length" class="linked-table">
        <table class="artifact-table">
          <thead>
            <tr>
              <th v-for="c in linkedColumns" :key="c">{{ c }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, i) in linkedRows"
              :key="i"
              :class="{ 'row-active': selectedKey !== null && selectedKey === rowKeyValue(row) }"
              @click="focusRow(row)"
            >
              <td v-for="(c, j) in linkedColumns" :key="c">{{ cellValue(row, c, j) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 表格结果 -->
    <div v-else-if="artifact.kind === 'table'" class="table-card">
      <div class="artifact-head">
        <Icon name="chart" :size="14" />
        <span>{{ artifactLabel(artifact.name, artifact.kind) }}</span>
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
import OlMap from 'ol/Map'  // 不要命名为 Map：会遮蔽 JS 内置 Map（featureIndex 用的是内置 Map）
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import { Fill, Stroke, Style } from 'ol/style'
import View from 'ol/View'
import ChartView from './ChartView.vue'
import Icon from './Icon.vue'
import { artifactLabel } from '../artifactLabels'
import { MAP_EXTENT, MAP_MAX_ZOOM, MAP_PROJECTION, createBaseLayers } from '../mapConfig'

const props = defineProps({
  artifact: { type: Object, required: true },
})

const mapEl = ref(null)
const copied = ref(false)
let map = null
let copyTimer = null
let highlightSource = null
// 地图 artifact 可内嵌 linked_table：表格行 ←→ GeoJSON 要素
// 必须是 JS 内置 Map（OL 的 Map 以 OlMap 名字导入，避免同名遮蔽）
const featureIndex = new Map()
const selectedKey = ref(null)

const columns = computed(() => props.artifact.data?.columns || [])
const rows = computed(() => props.artifact.data?.rows || [])
const filename = computed(() => props.artifact.data?.filename || props.artifact.name || '文件')
const fileUrl = computed(() => props.artifact.data?.url || '')
const linkedTable = computed(() => props.artifact.data?.linked_table || null)
const linkedColumns = computed(() => linkedTable.value?.columns || [])
const linkedRows = computed(() => linkedTable.value?.rows || [])

// 兼容两种行格式：对象行按列名取值，数组行按下标取值
function cellValue(row, column, index) {
  if (Array.isArray(row)) return row[index]
  return row[column]
}

// 表格行的主键值（linked_table.row_key 对应列名）
function rowKeyValue(row) {
  const key = linkedTable.value?.row_key
  if (!key || row === null || row === undefined) return null
  const value = Array.isArray(row) ? row[0] : row[key]
  return value === null || value === undefined ? null : String(value)
}

function clearHighlight() {
  selectedKey.value = null
  highlightSource?.clear()
}

// 点表格行：定位到对应图斑并高亮；再次点击同一行取消
function focusRow(row) {
  const value = rowKeyValue(row)
  const key = linkedTable.value?.feature_key
  if (value === null || !key || !map || !highlightSource) return
  const feature = featureIndex.get(value)
  if (!feature) return
  if (selectedKey.value === value) {
    clearHighlight()
    return
  }
  selectedKey.value = value
  highlightSource.clear()
  highlightSource.addFeature(feature)
  const geometry = feature.getGeometry()
  if (geometry) {
    map.getView().fit(geometry.getExtent(), {
      padding: [48, 48, 48, 48],
      maxZoom: 17,
      duration: 300,
    })
  }
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
  // 底图是天地图经纬度瓦片网格：数据直接按 EPSG:4326 读入，不做重投影
  const features = new GeoJSON().readFeatures(props.artifact.data, {
    dataProjection: MAP_PROJECTION,
    featureProjection: MAP_PROJECTION,
  })
  if (!features.length) return

  const source = new VectorSource({ features })
  // 关联明细表：建立 主键 → 要素 索引，并准备一个高亮图层
  const table = linkedTable.value
  if (table?.feature_key) {
    for (const feature of features) {
      const value = feature.get(table.feature_key)
      if (value !== null && value !== undefined) featureIndex.set(String(value), feature)
    }
  }
  highlightSource = new VectorSource()
  const highlightLayer = new VectorLayer({
    source: highlightSource,
    style: new Style({
      stroke: new Stroke({ color: '#ff1f1f', width: 4 }),
      fill: new Fill({ color: 'rgba(255, 31, 31, 0.35)' }),
    }),
  })
  highlightLayer.setZIndex(99)
  // 图层样式可随 GeoJSON 一起下发（预警图斑为红色描边 + 半透明红填充）
  const styleSpec = props.artifact.data?.style
  const vectorLayer = new VectorLayer({ source })
  // 数据图层盖在底图与注记之上
  vectorLayer.setZIndex(10)
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
  map = new OlMap({
    target: mapEl.value,
    layers: [...createBaseLayers(), vectorLayer, highlightLayer],
    view: new View({
      projection: MAP_PROJECTION,
      extent: MAP_EXTENT,
      center: [120.5, 29],
      zoom: 8,
      minZoom: 1,
      maxZoom: MAP_MAX_ZOOM,
    }),
    controls: [],
  })
  map.getView().fit(source.getExtent(), { padding: [24, 24, 24, 24], maxZoom: 17 })

  // 反向联动：点图斑高亮并在右侧表格里标出对应行；点空白处取消
  if (table?.feature_key) {
    map.on('singleclick', (event) => {
      let hit = null
      map.forEachFeatureAtPixel(event.pixel, (feature) => {
        hit = feature
        return true
      })
      if (!hit) {
        clearHighlight()
        return
      }
      const value = String(hit.get(table.feature_key))
      selectedKey.value = value
      highlightSource.clear()
      highlightSource.addFeature(hit)
    })
  }
})

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
  featureIndex.clear()
  highlightSource = null
  if (map) {
    map.setTarget(undefined)
    map = null
  }
})
</script>
