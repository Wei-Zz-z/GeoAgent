<template>
  <aside class="facility-panel">
    <h3>养老设施目录</h3>
    <p>{{ region || '加载中' }} · 资料收录{{ facilities.length }}家（非完整现状名录）</p>
    <p class="notice">区县按现有坐标与边界暂定；坐标及通行条件待核实。无数据不代表当地没有机构。</p>
    <details><summary>现有能力与待补数据</summary>
      <p>已有：机构选点、步行/简化驾车路网、概略覆盖多边形、设施点叠加、区县面积相交。</p>
      <p>社区统计待补：社区办公点经纬度或社区边界。现有4点仅为方法试验，不能代表全部社区。</p>
      <p>已接入2020年总人口网格试算；老年人口仍待补，不能用总人口或区县面积比例代替。</p>
      <p>精度改进待补：机构入口、坐标系确认、路网通行限制及水体、围墙等障碍。</p>
    </details>
    <label>① 选择区县
      <select v-model="districtId" aria-label="选择区县">
        <option value="">请选择区县</option>
        <option v-for="district in districts" :key="district.id" :value="district.id">{{ district.name }}（{{ facilities.filter(f => f.district_id === district.id).length }}家）</option>
      </select>
    </label>
    <template v-if="districtId && divisions">
      <label>乡镇 / 街道（可选，按地址筛选）
        <select v-model="streetId" aria-label="选择乡镇街道">
          <option value="">全部街道</option><option value="unknown">地址归属待核实</option>
          <option v-for="street in streets" :key="street.code" :value="street.code">{{ street.name }}</option>
        </select>
      </label>
      <label v-if="streetId && streetId !== 'unknown'">村 / 社区（可选）
        <select v-model="villageId" aria-label="选择村社区">
          <option value="">全部村社区</option><option value="unknown">地址归属待核实</option>
          <option v-for="village in villages" :key="village.code" :value="village.code">{{ village.name }}</option>
        </select>
      </label>
      <p class="notice">区划参考截至{{ divisions.data_as_of }}；仅按地址文字关联，未核实空间归属，不改变分析坐标。机构未匹配时可选“全部街道”查找。</p>
    </template>
    <p v-if="divisionError" class="notice">{{ divisionError }}；仍可按区县选择机构。</p>
    <input v-model="query" type="search" placeholder="搜索机构名称或地址" aria-label="搜索养老设施" />
    <label>出行模式 <select v-model="mode"><option value="walking">步行</option><option value="driving">驾车（简化试算）</option></select></label>
    <label>统计研究区<select v-model="studyArea"><option value="facility_district">设施所属区县</option><option value="hangzhou">杭州市范围</option></select></label>
    <label>假设速度（公里/小时） <input v-model.number="speed" type="number" :min="mode === 'walking' ? 1 : 5" :max="mode === 'walking' ? 6 : 60" step="0.1" /></label>
    <p v-if="mode === 'driving'" class="notice">默认30公里/小时；未计拥堵、信号灯、转向禁令及逐路限速。切换模式后需重新分析。</p>
    <p v-if="loading">正在读取目录…</p>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重新加载</button></p>
    <p v-if="message" role="status">{{ message }}</p>
    <p v-if="!loading && !error && !districtId">请先选择区县，再从下方机构列表点击分析。</p>
    <div v-if="!loading && !error && districtId" class="facility-table">
      <p>② 选择养老机构 · 当前显示{{ filtered.length }}家</p>
      <table>
        <thead><tr><th>机构 / 区县 / 地址</th><th>操作</th></tr></thead>
        <tbody><tr v-for="facility in filtered" :key="facility.id">
          <td><strong>{{ facility.name }}</strong><small>{{ facility.region }}</small><small>{{ facility.address || '地址未记录' }}</small><small :class="{ notice: !facility.available }">{{ facility.status }}</small><small v-if="divisions">{{ divisions.assignments[facility.id]?.status }}</small></td>
          <td><button :disabled="!facility.available || chat.streaming || sending" @click="analyze(facility)">{{ facility.available ? '分析' : '暂不可用' }}</button></td>
        </tr></tbody>
      </table>
      <p v-if="!filtered.length">当前筛选下无已匹配机构，不代表当地没有养老设施。可选择全部街道、全部村社区或清空搜索词查看。</p>
    </div>
  </aside>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { chat } from '../stores/chat'

const facilities = ref([])
const districts = ref([])
const districtId = ref('')
const divisions = ref(null)
const divisionError = ref('')
const streetId = ref('')
const villageId = ref('')
const streets = computed(() => divisions.value?.streets.filter(s => s.areaCode === districtId.value) || [])
const villages = computed(() => divisions.value?.villages.filter(v => v.streetCode === streetId.value) || [])
const region = ref('')
const query = ref('')
const loading = ref(false)
const error = ref('')
const message = ref('')
const sending = ref(false)
const mode = ref('walking')
const studyArea = ref('facility_district')
const speed = ref(4.2)
watch(mode, value => { speed.value = value === 'driving' ? 30 : 4.2 })
watch(districtId, () => { query.value = ''; message.value = ''; streetId.value = ''; villageId.value = '' })
watch(streetId, () => { villageId.value = '' })
function matchesDivision(facility) {
  const assignment = divisions.value?.assignments[facility.id] || {}
  return (!streetId.value || (streetId.value === 'unknown' ? !assignment.street_code : assignment.street_code === streetId.value)) &&
    (!villageId.value || (villageId.value === 'unknown' ? !assignment.village_code : assignment.village_code === villageId.value))
}
const filtered = computed(() => facilities.value.filter(facility => facility.district_id === districtId.value &&
  matchesDivision(facility) &&
  `${facility.name} ${facility.address} ${facility.region}`.includes(query.value.trim())))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/elder/facilities')
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '目录读取失败')
    facilities.value = data.facilities
    districts.value = data.districts
    region.value = data.region
    try {
      const divisionResponse = await fetch('/api/elder/divisions')
      const reference = await divisionResponse.json()
      if (!divisionResponse.ok) throw new Error(reference.detail || '区划参考加载失败')
      divisions.value = reference
      divisionError.value = ''
    } catch (err) { divisions.value = null; divisionError.value = err.message }
  } catch (err) { error.value = `无法读取机构目录：${err.message}` }
  finally { loading.value = false }
}

function analyze(facility) {
  if (!facility.available) { message.value = facility.status; return }
  const minimum = mode.value === 'walking' ? 1 : 5
  const maximum = mode.value === 'walking' ? 6 : 60
  if (!Number.isFinite(speed.value) || speed.value < minimum || speed.value > maximum) {
    message.value = `请输入${minimum}至${maximum}公里/小时的速度。`
    return
  }
  const label = mode.value === 'driving' ? '驾车' : '步行'
  const sent = chat.sendMessage(`请检索并分析${facility.name}（${facility.address}，设施编号${facility.id}）从设施出发5、10、15、20、25、30分钟的${label}可达范围，模式${mode.value}，假设速度${speed.value}公里/小时。统计研究区选择：${studyArea.value === 'hangzhou' ? '杭州市范围' : '设施所属区县'}。`, { agent: 'elder_care' })
  sending.value = sent
  message.value = sent ? '已发送到中间对话，请查看分析过程。' : '当前对话尚未连接或正在处理，请稍后再试。'
}
watch(() => [chat.streaming, chat.error], ([streaming, error]) => {
  if (streaming || error) sending.value = false
})
onMounted(load)
</script>

<style scoped>
.facility-panel { width: 360px; min-width: 280px; max-width: 38vw; padding: 16px; overflow-y: auto; border-left: 1px solid #e2e8f0; background: #f8fafc; }
h3 { margin: 0 0 10px; } p { font-size: 12px; line-height: 1.6; }
.notice { color: #64748b; }
input { box-sizing: border-box; width: 100%; padding: 9px; border: 1px solid #cbd5e1; border-radius: 6px; }
label { display: block; margin: 12px 0; font-size: 13px; }
select { box-sizing: border-box; width: 100%; margin-top: 6px; padding: 8px; border: 1px solid #cbd5e1; border-radius: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 12px; }
th, td { text-align: left; padding: 10px 4px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
small { display: block; margin-top: 5px; color: #64748b; }
button { cursor: pointer; white-space: nowrap; padding: 6px; } button:disabled { cursor: wait; opacity: .5; }
</style>
