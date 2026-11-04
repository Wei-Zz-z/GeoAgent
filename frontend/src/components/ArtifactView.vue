<template>
  <div class="artifact">
    <!-- 地图结果 -->
    <div v-if="artifact.kind === 'geojson'" class="map-card" :class="{ 'access-expanded': expanded, 'access-studio': isElderAccess }" :role="expanded ? 'dialog' : undefined" :aria-label="expanded ? '养老设施可达性地图' : undefined">
      <div class="artifact-head">
        <Icon name="map-pin" :size="14" />
        <span>{{ artifact.name || '空间分布图' }}</span>
        <div v-if="isElderAccess" class="access-actions">
          <button type="button" :aria-pressed="showSummary" @click="toggleSummary">{{ showSummary ? '收起统计' : '显示统计' }}</button>
          <button type="button" @click="returnToFacility">回到设施</button>
          <button type="button" :aria-pressed="expanded" @click="toggleExpanded">{{ expanded ? '收起地图（Esc）' : '放大查看' }}</button>
        </div>
      </div>
      <div v-if="isElderAccess" class="access-display"><label><input v-model="showAllTimes" type="checkbox" /> 多时长叠加</label><small>{{ showAllTimes ? '各档范围按颜色叠加，道路与统计仍对应所选时长' : '单时长查看，拖动滑动条切换范围' }}</small></div>
      <div v-if="isElderAccess" class="access-time">
        <span>{{ travelLabel }}时长：<strong>{{ activeMinutes }} 分钟</strong> · {{ showAllTimes ? '道路与统计的当前档' : '仅显示当前时长' }}<span v-if="artifact.data.speed_kmh"> · 假设速度{{ artifact.data.speed_kmh }}公里/小时</span></span>
        <input v-model.number="accessIndex" type="range" min="0" :max="Math.max(0, accessTimes.length - 1)" step="1"
          :disabled="accessTimes.length < 2" :aria-valuetext="`${activeMinutes}分钟`" aria-label="选择出行时长" />
        <div class="access-ticks"><button v-for="minutes in accessTimes" :key="minutes" type="button" :aria-pressed="minutes === activeMinutes" :style="{ '--time-color': accessColor(minutes) }" @click="selectTime(minutes)">{{ minutes }}<small> 分钟</small></button></div>
      </div>
      <div class="access-workspace" :class="{ 'with-summary': isElderAccess && showSummary }">
        <div ref="mapEl" class="mini-map"></div>
        <ElderAccessSummary v-if="isElderAccess && showSummary" :data="artifact.data" :stats="activeStats" :minutes="activeMinutes" :travel-label="travelLabel" :boundary-codes="boundaryCodes" :selected-boundary="selectedBoundary" @select-boundary="selectBoundary" @select-time="selectTime" />
      </div>
      <label v-if="isElderAccess && artifact.data.district_reference?.available" class="access-layer-toggle"><input v-model="showUncovered" type="checkbox" /> 显示模型未覆盖部分（灰色）</label>
      <div v-if="isElderAccess" class="access-legend">
        <template v-if="showAllTimes"><span v-for="minutes in accessTimes" :key="minutes"><i :style="{ background: accessColor(minutes) }"></i>{{ minutes }}分钟</span><span>实线道路与右侧统计：{{ activeMinutes }}分钟</span></template>
        <span v-else><i :style="{ background: accessColor(activeMinutes) }"></i>{{ activeMinutes }}分钟：实线为可达道路，色块为{{ artifact.data.coverage_method ? '概略覆盖多边形' : '道路展示走廊' }}</span>
        <span><b class="legend-origin" aria-hidden="true"></b>设施起点</span><span><b class="legend-link" aria-hidden="true"></b>假设接入段</span>
        <span v-if="selectedBoundary"><b class="legend-boundary" aria-hidden="true"></b>选中的历史边界</span>
      </div>
      <ElderAccessNotes v-if="isElderAccess" :data="artifact.data" />
      <template v-if="isElderAccess">
        <details v-if="artifact.data.population_reference"><summary>2020年总人口估算（非老年人口）</summary>
          <template v-if="artifact.data.population_reference.available">
            <p>研究区：{{ artifact.data.district_reference?.name }}；有数据网格人口合计：{{ artifact.data.population_reference.study_population }}人。</p>
            <p>本档概略面内约{{ activeStats?.covered_population_2020 }}人；模型未覆盖部分约{{ activeStats?.model_uncovered_population_2020 }}人；占比{{ activeStats?.population_coverage_percent == null ? '未计算' : activeStats.population_coverage_percent + '%' }}。</p>
            <p>{{ artifact.data.population_reference.method }}；有效网格{{ artifact.data.population_reference.valid_cells }}个，无数据网格{{ artifact.data.population_reference.missing_cells }}个。</p>
          </template>
        </details>
        <details v-if="artifact.data.village_reference"><summary>历史村级边界相交试算：{{ activeStats?.historical_village_unit_count ?? '无数据' }}个单元</summary>
          <p v-if="artifact.data.village_reference.available">其中原名含“社区”：{{ activeStats?.historical_community_named_count ?? '未记录' }}个；其余不统一视为社区。</p>
          <table v-if="activeStats?.historical_village_units?.length" class="artifact-table">
            <thead><tr><th>原县区 / 乡镇 / 名称</th><th>类型</th><th>相交面积（亩）</th><th>占该边界面积</th></tr></thead>
            <tbody><tr v-for="item in activeStats.historical_village_units" :key="item.code"><td>{{ item.county }} / {{ item.township }} / {{ item.name }}<br>{{ item.code }}</td><td>{{ item.kind }}</td><td>{{ areaMu(item.intersection_m2) }}</td><td>{{ item.intersection_percent }}%</td></tr></tbody>
          </table>
          <p v-else-if="artifact.data.village_reference.available">当前概略面与已加载历史村级边界无正面积相交。</p>
        </details>
        <details v-if="artifact.data.township_reference"><summary>历史乡镇街道相交试算：{{ activeStats?.historical_township_count ?? '无数据' }}个</summary>
          <table v-if="activeStats?.historical_townships?.length" class="artifact-table">
            <thead><tr><th>原县区 / 乡镇街道</th><th>相交面积（亩）</th><th>占该边界面积</th></tr></thead>
            <tbody><tr v-for="item in activeStats.historical_townships" :key="`${item.county}/${item.name}`"><td>{{ item.county }} / {{ item.name }}</td><td>{{ areaMu(item.intersection_m2) }}</td><td>{{ item.intersection_percent }}%</td></tr></tbody>
          </table>
          <p v-else-if="artifact.data.township_reference.available">当前概略面与已加载历史乡镇边界无正面积相交。</p>
        </details>
        <details v-if="artifact.data.district_reference"><summary>研究区面积叠加统计</summary>
          <template v-if="artifact.data.district_reference.available">
            <p>{{ artifact.data.district_reference.name }}全域面积：{{ areaMu(artifact.data.district_reference.area_m2, artifact.data.district_reference.area_mu) }}亩；当前概略覆盖面与该区县相交面积：{{ areaMu(activeStats?.district_intersection_m2) }}亩，占全域面积{{ activeStats?.district_coverage_percent }}%。</p>
            <p>所选设施、所选时间下研究区内模型未覆盖部分：{{ areaMu(activeStats?.model_uncovered_m2) }}亩。只扣除本次单设施概略面，不代表其他设施也不能服务。</p>
          </template><p v-else>{{ artifact.data.district_reference.notice }}</p>
        </details>
        <details v-if="artifact.data.community_reference"><summary>社区小样本试验（非全区统计）</summary>
          <p>当前参考点落入的关联社区：{{ activeStats?.community_proxy_count ?? '无数据' }}个。不能据此推断其他社区未覆盖。</p>
          <p v-for="community in activeStats?.community_proxy_names || []" :key="community.code">{{ community.name }} · {{ community.code }}</p>
        </details>
        <p class="access-selection" aria-live="polite">{{ selectedAccess || '点击设施、道路或彩色范围查看说明；可滚轮缩放、拖动地图。' }}</p>
        <details class="access-details">
          <summary>查看计算假设与辅助指标</summary>
          <p>{{ artifact.data.notice }}</p>
          <p v-for="row in artifact.data.statistics || []" :key="row.minutes">{{ row.minutes }}分钟：展示走廊面积 {{ areaMu(row.display_area_m2, row.display_area_mu) }} 亩（受20米展示宽度影响，不作为服务面积）。</p>
        </details>
      </template>
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import ElderAccessSummary from './ElderAccessSummary.vue'
import ElderAccessNotes from './ElderAccessNotes.vue'
import { areaMu } from '../elderAccessPresentation.js'
import { elderAccessStyle } from '../elderAccessStyle.js'

const props = defineProps({
  artifact: { type: Object, required: true },
})

const mapEl = ref(null)
const copied = ref(false)
const selectedAccess = ref('')
const expanded = ref(false)
const showUncovered = ref(false)
const showAllTimes = ref(false)
const showSummary = ref(true)
const selectedBoundary = ref('')
const boundaryCodes = computed(() => (props.artifact.data?.features || []).filter(feature => feature.properties?.kind === 'village_boundary').map(feature => feature.properties.code))
const accessIndex = ref(Math.max(0, [...(props.artifact.data?.thresholds || [])].sort((a, b) => a - b).indexOf(15)))
const accessTimes = computed(() => [...(props.artifact.data?.thresholds || [])].sort((a, b) => a - b))
const activeMinutes = computed(() => accessTimes.value[accessIndex.value])
const activeStats = computed(() => props.artifact.data?.statistics?.find(row => row.minutes === activeMinutes.value))
const travelLabel = computed(() => props.artifact.data?.travel_mode === 'driving' ? '驾车' : '步行')
let map = null
let facilityCoordinate = null
let accessLayer = null
let copyTimer = null

async function toggleSummary() {
  showSummary.value = !showSummary.value
  await nextTick()
  map?.updateSize()
}

async function toggleExpanded() {
  expanded.value = !expanded.value
  await nextTick()
  map?.updateSize()
}

function selectTime(minutes) {
  const index = accessTimes.value.indexOf(minutes)
  if (index >= 0) accessIndex.value = index
}

function selectBoundary(code) {
  if (selectedBoundary.value === code) {
    selectedBoundary.value = ''
    selectedAccess.value = ''
  } else {
    const feature = accessLayer?.getSource()?.getFeatures().find(item => item.get('kind') === 'village_boundary' && item.get('code') === code)
    if (!feature) return
    selectedBoundary.value = code
    selectedAccess.value = `${feature.get('name')} · 紫色线为原始历史边界的显示轮廓。`
    map?.getView().fit(feature.getGeometry().getExtent(), { padding: [35, 35, 35, 35], maxZoom: 16, duration: 250 })
  }
  accessLayer?.changed()
}

function returnToFacility() {
  if (facilityCoordinate) map?.getView().animate({ center: facilityCoordinate, duration: 250 })
}

function closeExpanded(event) {
  if (event.key === 'Escape' && expanded.value) toggleExpanded()
}

// 旧会话中的养老统计表只在显示时换算，不修改历史原始结果。
const legacyAreaColumn = computed(() => props.artifact.name === '可达道路统计（试算）' ? props.artifact.data?.columns?.indexOf('概略覆盖面积（平方米）') ?? -1 : -1)
const columns = computed(() => (props.artifact.data?.columns || []).map((column, index) => index === legacyAreaColumn.value ? '概略覆盖面积（亩）' : column))
const rows = computed(() => (props.artifact.data?.rows || []).map(row => {
  if (legacyAreaColumn.value < 0) return row
  if (Array.isArray(row)) return row.map((value, index) => index === legacyAreaColumn.value ? areaMu(value) : value)
  const { '概略覆盖面积（平方米）': area, ...rest } = row
  return { ...rest, '概略覆盖面积（亩）': areaMu(area) }
}))
const filename = computed(() => props.artifact.data?.filename || props.artifact.name || '文件')
const fileUrl = computed(() => props.artifact.data?.url || '')
const isElderAccess = computed(() => props.artifact.data?.analysis_type === 'elder_accessibility')

function accessColor(minutes) {
  const index = accessTimes.value.indexOf(minutes)
  return ['#15803d', '#d97706', '#2563eb', '#7c3aed', '#be185d', '#0e7490'][index] || '#2563eb'
}

function accessStyle(feature) {
  const minutes = showAllTimes.value && feature.get('kind') === 'area' ? feature.get('minutes') : activeMinutes.value
  return elderAccessStyle(feature, activeMinutes.value, accessColor(minutes), showUncovered.value, selectedBoundary.value, showAllTimes.value)
}

watch(showAllTimes, () => {
  selectedAccess.value = ''
  accessLayer?.changed()
})

watch(showUncovered, () => {
  selectedAccess.value = ''
  accessLayer?.changed()
  const features = accessLayer?.getSource()?.getFeatures() || []
  if (features.length) map?.getView().fit(new VectorSource({ features: showUncovered.value ? features.filter(f => f.get('kind') !== 'village_boundary') : features.filter(f => !['uncovered', 'study_area', 'village_boundary'].includes(f.get('kind'))) }).getExtent(), { padding: [24, 24, 24, 24], maxZoom: 17 })
})

watch(activeMinutes, () => {
  selectedBoundary.value = ''
  selectedAccess.value = ''
  accessLayer?.changed()
})

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
  facilityCoordinate = features.find(feature => feature.get('kind') === 'facility')?.getGeometry().getCoordinates()
  // 图层样式可随 GeoJSON 一起下发（预警图斑为红色描边 + 半透明红填充）
  const styleSpec = props.artifact.data?.style
  const vectorLayer = new VectorLayer({ source })
  if (isElderAccess.value) {
    accessLayer = vectorLayer
    vectorLayer.setStyle(accessStyle)
  } else if (styleSpec) {
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
    layers: [new TileLayer({ source: new OSM(), opacity: isElderAccess.value ? 0.45 : 1 }), vectorLayer],
    view: new View({ center: fromLonLat([116.4, 39.9]), zoom: 10 }),
    controls: [],
  })
  const initialSource = isElderAccess.value ? new VectorSource({ features: features.filter(f => !['uncovered', 'study_area', 'village_boundary'].includes(f.get('kind'))) }) : source
  map.getView().fit(initialSource.getExtent(), { padding: [24, 24, 24, 24], maxZoom: 17 })
  if (isElderAccess.value) window.addEventListener('keydown', closeExpanded)
  if (isElderAccess.value) map.on('singleclick', (event) => {
    const feature = map.forEachFeatureAtPixel(event.pixel, (item) => item, { hitTolerance: 6 })
    if (!feature) { selectedAccess.value = ''; return }
    const kind = feature.get('kind')
    selectedAccess.value = kind === 'facility' ? `${feature.get('name')} · 分析起点，坐标与实际入口待核实。`
      : kind === 'connector' ? '设施到道路的假设直线接入段，距离已计入耗时，实际通行待核实。'
      : kind === 'village_boundary' ? `${feature.get('name')} · 原始历史边界的显示轮廓。`
      : kind === 'study_area' ? '研究区：设施所属区县的现有边界文件，不代表所有设施的联合服务范围。'
      : kind === 'uncovered' ? `${feature.get('minutes')}分钟：所属区县扣除本次单设施概略面的模型未覆盖部分，不是真实服务盲区。`
      : `${feature.get('minutes')}分钟${kind === 'road' ? '可达道路集合（含更短时长可达路段），不是单一路径。' : props.artifact.data.coverage_method ? '概略覆盖多边形：路段包络会填补间隙，未核验障碍和地块入口，不代表面内所有位置可达。' : '道路两侧20米展示走廊，不代表整个面内均可达。'}`
  })
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', closeExpanded)
  if (copyTimer) clearTimeout(copyTimer)
  if (map) {
    map.setTarget(undefined)
    map = null
  }
  accessLayer = null
})
</script>

<style scoped>
.access-legend { display: flex; flex-wrap: wrap; gap: 12px; padding: 10px 12px; font-size: 12px; }
.access-legend span { display: inline-flex; align-items: center; gap: 5px; }
.access-legend i { width: 12px; height: 12px; border-radius: 2px; }
.access-selection, .access-details { margin: 0; padding: 0 12px 12px; font-size: 12px; line-height: 1.6; }
.access-details summary { cursor: pointer; }
.access-actions { margin-left: auto; display: flex; gap: 8px; }
.access-actions button { cursor: pointer; padding: 5px 8px; border: 1px solid #cbd5e1; border-radius: 6px; background: #fff; color: #334155; }
.map-card { background: #fff; container-type: inline-size; }
.access-expanded { position: fixed; inset: 16px; z-index: 1000; overflow: auto; box-shadow: 0 0 0 20px #0f172acc; border-radius: 12px; }
.access-workspace.with-summary { display: grid; grid-template-columns: minmax(0, 1fr) 270px; gap: 12px; padding: 0 12px 12px; align-items: stretch; }
.mini-map { min-width: 0; }
.with-summary .mini-map { height: 730px; border-radius: 12px; min-width: 0; }
.access-expanded .mini-map { height: 72vh; min-height: 500px; }
@container (max-width: 620px) { .access-workspace.with-summary { grid-template-columns: minmax(0, 1fr); } .with-summary .mini-map { height: 390px; } }
@media (max-width: 900px) { .access-workspace.with-summary { grid-template-columns: minmax(0, 1fr); } .with-summary .mini-map { height: 390px; } }
@media (max-width: 600px) { .access-expanded { inset: 4px; } .access-actions { flex-wrap: wrap; } }
.access-time { display: grid; gap: 8px; padding: 12px; font-size: 13px; }
.access-time input { width: 100%; margin: 0; cursor: pointer; }
.access-ticks { display: flex; justify-content: space-between; color: var(--text-3); font-size: 12px; }
.access-display { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 12px 0; color: #475569; font-size: 12px; }
.access-display label { cursor: pointer; display: inline-flex; align-items: center; gap: 5px; }
.access-display small { color: #64748b; font-size: 11px; }
.access-studio { border: 1px solid #d7e5e3; border-radius: 16px; overflow: hidden; background: #f5f8f7; box-shadow: 0 8px 28px #153c3810; }
.access-studio > .artifact-head { padding: 16px 18px; background: #163e3b; color: #f0fdfa; border: 0; flex-wrap: wrap; gap: 10px; font-weight: 600; }
.access-studio .access-actions button { background: #ffffff0d; color: #e1f3ee; border-color: #ffffff30; border-radius: 8px; padding: 7px 10px; font-size: 11px; }
.access-studio .access-actions button:hover { background: #ffffff22; }
.access-studio .access-display { padding: 16px 18px 0; justify-content: space-between; }
.access-studio .access-display label { padding: 7px 11px; background: #fff; border: 1px solid #d7e5e3; border-radius: 8px; font-weight: 600; color: #215b51; }
.access-studio input { accent-color: #147d6c; }
.access-studio .access-time { padding: 14px 18px 18px; gap: 12px; color: #64748b; font-size: 11px; }
.access-studio .access-time strong { font-size: 18px; color: #163e3b; margin: 0 4px; }
.access-studio .access-time input { height: 18px; }
.access-studio .access-ticks { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px; }
.access-ticks button { padding: 7px 2px; border: 1px solid #dce7e4; border-radius: 7px; background: #fff; color: #526760; cursor: pointer; font-weight: 600; font-size: 14px; }
.access-ticks button small { font-size: 10px; font-weight: normal; }
.access-ticks button[aria-pressed="true"] { color: #fff; background: var(--time-color); border-color: var(--time-color); box-shadow: 0 3px 8px #0f172a20; }
.access-studio .mini-map { border: 1px solid #d7e5e3; border-radius: 12px; }
.access-studio .access-layer-toggle { display: flex; align-items: center; gap: 6px; padding: 0 18px; font-size: 11px; color: #526760; }
.access-studio .access-legend { padding: 12px 18px; gap: 8px 16px; font-size: 11px; color: #526760; }
.access-studio .access-legend i { border-radius: 50%; width: 9px; height: 9px; }
.access-legend .legend-origin { width: 9px; height: 9px; border-radius: 50%; background: #dc2626; box-shadow: 0 0 0 2px white, 0 0 0 3px #e6cece; }
.access-legend .legend-link, .access-legend .legend-boundary { width: 19px; border-top: 2px dashed #dc2626; }
.access-legend .legend-boundary { border-color: #7c3aed; }
.access-studio .access-legend span { background: white; padding: 4px 8px; border-radius: 6px; border: 1px solid #dce7e4; }
.access-studio > details { margin: 0 12px; padding: 10px 8px; border-bottom: 1px solid #e1e9e6; font-size: 12px; color: #526760; }
.access-studio > details > summary { cursor: pointer; }
.access-studio .access-selection { margin: 10px 12px; padding: 10px 12px; border-radius: 8px; background: #eaf1ef; color: #41665c; font-size: 11px; }
.access-studio button:focus-visible { outline: 3px solid #38bdf8; outline-offset: 2px; }
@container (max-width: 620px) { .access-studio .access-actions { margin-left: 0; } .access-studio .access-display { align-items: flex-start; gap: 8px; } }
</style>
