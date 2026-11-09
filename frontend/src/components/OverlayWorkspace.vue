<template>
  <main class="overlay-workspace" :class="{ 'is-embedded': embedded }">
    <a v-if="!embedded" href="/">返回模板与问数</a>
    <h1 v-if="!embedded">套合入库</h1>
    <p class="overlay-desc">
      上传后时相、前时相（必填）与永久基本农田、生态保护红线（可选）。系统按后时相边界切分前时相，
      挂接母斑属性、标记永农／红线压盖并重算面积，生成一期套合表写入数据库。
    </p>

    <section class="overlay-layers">
      <div v-for="layer in layers" :key="layer.key" class="overlay-layer">
        <div class="overlay-layer-head">
          <strong>{{ layer.label }}</strong>
          <span v-if="layer.required" class="overlay-required">必填</span>
        </div>
        <p class="overlay-hint">{{ layer.hint }}</p>
        <input
          type="file"
          multiple
          :accept="ACCEPT"
          :disabled="busy"
          @change="pick(layer.key, $event)"
        />
        <p class="overlay-files">{{ fileText(layer.key) }}</p>
      </div>
    </section>

    <p class="overlay-note">
      提示：Shapefile 请一次选中整套文件（.shp/.shx/.dbf/.prj/.cpg），或直接上传 ZIP；只选 .shp 会缺属性表和坐标系。
    </p>
    <section class="overlay-meta">
      <label>年份
        <select v-model.number="year" :disabled="busy">
          <option v-for="item in years" :key="item" :value="item">{{ item }} 年</option>
        </select>
      </label>
      <label>期次
        <select v-model.number="round" :disabled="busy">
          <option v-for="item in rounds" :key="item" :value="item">第{{ cn[item] || item }}期</option>
        </select>
      </label>
      <label class="overlay-table">表名
        <input v-model.trim="tableName" :disabled="busy" spellcheck="false" @input="tableTouched = true" />
      </label>
      <label class="overlay-check">
        <input type="checkbox" v-model="overwrite" :disabled="busy" /> 覆盖同名表
      </label>
    </section>

    <p class="overlay-note">将登记为：{{ label }} → data."{{ tableName }}"</p>
    <p class="overlay-note">未上传永农／红线时，对应字段统一记为“否”。</p>

    <button class="overlay-start" :disabled="busy || !canStart" @click="start">
      {{ busy ? '套合中…' : '开始套合' }}
    </button>

    <p v-if="busy" class="overlay-progress">[{{ progress }}%] {{ stage }} {{ detail }}</p>
    <p v-if="error" class="overlay-error" role="alert">{{ error }}</p>
    <OverlayResult v-if="result" :data="result" />

    <OverlayDataManager ref="manager" />
  </main>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import OverlayResult from './OverlayResult.vue'
import OverlayDataManager from './OverlayDataManager.vue'

const props = defineProps({ embedded: { type: Boolean, default: false } })

const ACCEPT = '.zip,.shp,.shx,.dbf,.prj,.cpg,.qmd,.geojson,.json'
const KEYS = ['hsx', 'qsx', 'yjn', 'hx']
const cn = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二']

const layers = ref([])
const yearOptions = ref([])
const roundOptions = ref([1, 2, 3, 4])
const files = ref({ hsx: [], qsx: [], yjn: [], hx: [] })
const year = ref(new Date().getFullYear())
const round = ref(1)
const tableName = ref('')
const tableTouched = ref(false)
const overwrite = ref(false)
const manager = ref(null)
const busy = ref(false)
const stage = ref('')
const detail = ref('')
const progress = ref(0)
const error = ref('')
const result = ref(null)

const years = computed(() => (yearOptions.value.length ? yearOptions.value : [year.value]))
const rounds = computed(() => (roundOptions.value.length ? roundOptions.value : [1, 2, 3, 4]))
const label = computed(() => `${year.value}年第${cn[round.value] || round.value}期`)
const canStart = computed(() => Boolean(files.value.hsx.length && files.value.qsx.length && tableName.value))

function pick(key, event) {
  files.value[key] = Array.from(event.target.files || [])
  error.value = ''
}

function fileText(key) {
  const list = files.value[key]
  return list.length ? list.map((file) => file.name).join('、') : '未选择文件'
}

function syncTableName() {
  if (!tableTouched.value) tableName.value = `${year.value}_${round.value}_change_landuse`
}

watch([year, round], syncTableName)

async function load() {
  try {
    const [options, datasets] = await Promise.all([
      fetch('/api/overlay/options').then((r) => r.json()),
      fetch('/api/overlay/datasets').then((r) => r.json()).catch(() => ({ datasets: [] })),
    ])
    layers.value = options.layers || []
    yearOptions.value = options.years || []
    roundOptions.value = options.rounds || [1, 2, 3, 4]
    const currentYear = options.defaults?.year || new Date().getFullYear()
    const used = new Set((datasets.datasets || []).filter((d) => d.year === currentYear).map((d) => d.round))
    const free = roundOptions.value.find((item) => !used.has(item)) || 1
    year.value = currentYear
    round.value = free
    syncTableName()
  } catch (err) {
    error.value = `读取套合选项失败：${err.message}`
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function poll(jobId) {
  for (;;) {
    await sleep(1200)
    const response = await fetch(`/api/overlay/jobs/${jobId}`)
    const job = await response.json()
    if (!response.ok) throw new Error(job.detail || '查询任务失败')
    progress.value = job.progress || 0
    stage.value = job.stage || ''
    detail.value = job.detail || ''
    if (job.status === 'done') {
      result.value = job.result
      manager.value?.load()
      return
    }
    if (job.status === 'failed') throw new Error(job.error || '套合失败')
  }
}

async function start() {
  if (!canStart.value) return
  busy.value = true
  error.value = ''
  result.value = null
  progress.value = 0
  stage.value = '提交中'
  detail.value = ''
  try {
    const body = new FormData()
    for (const key of KEYS) for (const file of files.value[key]) body.append(key, file)
    body.append('table_name', tableName.value)
    body.append('year', String(year.value))
    body.append('round', String(round.value))
    body.append('overwrite', overwrite.value ? '1' : '0')
    const response = await fetch('/api/overlay/build', { method: 'POST', body })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '提交失败')
    await poll(data.job_id)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>