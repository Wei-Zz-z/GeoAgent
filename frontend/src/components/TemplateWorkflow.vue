<template>
  <section class="template-workflow">
    <h2>模板生成报告</h2>
    <p>上传 DOCX；仅在无法可靠匹配时请您选择。</p>
    <p><a href="/api/report-templates/sample">下载模板库示例</a></p>
    <input type="file" accept=".docx" :disabled="busy || chat.streaming" @change="upload" />
    <p v-if="busy">正在解析模板…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="result">
      <div class="workflow-summary">
        <p>已识别 {{ result.template.slots.length }} 处待填位置，整理为 {{ result.questions.length }} 组问数问题。</p>
        <template v-if="result.supported_template_kind">
          <p>已识别为{{ templateName(result.supported_template_kind) }}，将按该模板已核验的统计口径查询并回填。</p>
          <p v-if="autoBusy">正在查询并生成核验版…</p>
          <p v-if="generated">核验版已生成：<a :href="generated.word.url" download>下载 Word</a><template v-if="generated.pdf"> · <a :href="generated.pdf.url" download>下载 PDF</a></template><span v-else>；PDF 暂不可用：{{ generated.pdf_error }}</span></p>
          <p v-if="generated?.summary">已填正文 {{ generated.summary.filled_scalars }} 处、表格 {{ generated.summary.filled_tables }} 张、图表 {{ generated.summary.charts }} 张。</p>
          <p v-if="pendingItems.length" class="warning">待核实：{{ pendingItems.join('、') }}</p>
          <button v-if="!generated && !autoBusy" @click="autoGenerateSupported">重新尝试生成</button>
          <p v-if="autoError" role="alert">{{ autoError }}</p>
        </template>
        <template v-else>
          <p>已查询 {{ queriedCount }} 组问题，还有 {{ unmatchedQuestions.length }} 组需确认。系统按指标、表头及单位绑定结果；无法确定的位置保留“待核实”。</p>
          <details v-for="q in unmatchedQuestions" :key="q.id" class="question-choice">
            <summary>{{ q.id }} · {{ q.question.slice(0, 55) }}</summary>
            <p>模板原文：{{ q.source_text }}</p>
            <button v-for="candidate in candidates(q).slice(0, 3)" :key="candidate.id" :disabled="q.executing || chat.streaming || !candidate.compatible" @click="chooseCandidate(q, candidate.id)">{{ candidate.question }}（{{ candidate.retrieval_source === 'vector' ? '向量补充候选，' : '' }}文字规则分 {{ Math.round(candidate.confidence * 100) }}%{{ candidate.compatible ? '' : '，口径不适用' }}）</button>
            <button :disabled="q.executing || chat.streaming" @click="chooseFree(q)">都不合适，交给智能体自由问数</button>
            <p v-if="q.directAsked" class="warning">自由问数答案仅供参考；无法确定与待填位置一一对应时保留“待核实”。{{ q.captureNotice }}</p>
            <button v-if="q.directAsked && !chat.streaming" @click="captureFree(q)">核对最近回答的回填位置</button>
            <p v-if="q.askError" role="alert">{{ q.askError }}</p>
          </details>
          <button :disabled="generating || chat.streaming" @click="generatePending">{{ generating ? '正在生成…' : '生成 Word（未确定项待核实）' }}</button>
          <p v-if="error" role="alert">{{ error }}</p>
          <p v-if="generated">核验版已生成：<a :href="generated.word.url" download>下载 Word</a><template v-if="generated.pdf"> · <a :href="generated.pdf.url" download>下载 PDF</a></template><span v-else>；PDF 暂不可用：{{ generated.pdf_error }}</span>。未绑定位置保持待核验；自由问数回填仅供参考。</p>
          <p v-if="generated?.summary?.pending_items?.length" class="warning">待核实 {{ generated.summary.pending_items.length }} 项：{{ generated.summary.pending_items.join('、') }}</p>
          <p>其余回填或定位细节可在下方“高级检查”核对。</p>
        </template>
      </div>
      <details class="advanced-inspection">
        <summary>高级检查：原文、问题、回填位置与测试生成</summary>
      <p>模板解析完成：识别到{{ result.template.sections.length }}个章节、{{ result.template.blocks.length }}处正文或表格、{{ result.template.slots.length }}个逻辑槽位、{{ result.atomic_items.length }}个原子查询项，并整理出{{ result.questions.length }}组问题。</p>
      <p>图表锚点：{{ result.template.chart_anchors.length }}个。每个原子查询项都保存了来源和回填位置。</p>
      <p>本次业务期为{{ result.template.report_year }}年，解析时间为{{ formatTime(result.template.parsed_at) }}。</p>
      <button @click="download">下载模板解析结果和问数清单</button>
      <details><summary>查看模板解析结果（JSON）</summary><pre>{{ JSON.stringify(result.template, null, 2) }}</pre></details>
      <details><summary>查看正文、表格和图表定位</summary>
        <p>编号 b0、b1…按模板原有顺序排列；每个待填位置与原文块绑定。生成时先核对原文件未变化，再按定位信息回填。</p>
        <ul>
          <li v-for="slot in result.template.slots" :key="slot.id">
            <strong>{{ slot.id }}</strong> · {{ slot.kind === 'table_region' ? '表格区域' : '正文位置' }} · {{ slot.block_id }}
            <span v-if="slot.kind === 'table_region'"> · 可填单元格{{ slot.target_cell_ids.length }}个</span>
            <span v-else> · 原文“{{ slot.text }}”</span>
          </li>
          <li v-for="anchor in result.template.chart_anchors" :key="anchor.id">
            <strong>{{ anchor.id }}</strong> · 图{{ anchor.figure_number }} · {{ anchor.caption_block }} · {{ anchor.placement === 'replace_existing_image' ? '替换原图' : '插在图题前' }} · 建议{{ anchor.chart_type_hint === 'pie' ? '饼图' : '柱状图' }}
          </li>
        </ul>
        <p>图表类型和数据来源仍需人工确认；锚点只是建议位置，不代表已完成绘图。</p>
      </details>
      <p v-for="warning in result.template.warnings" :key="warning">{{ warning }}</p>
      <h3>提取完整性核对</h3>
      <details><summary>问题是怎样提取和核对的？</summary>
        <p>系统按Word中的原有顺序读取段落和表格，先找数字占位符、待填表格等位置，再结合附近原文、表头和单位整理问题。每组问题保留原文、来源编号及回填位置；标准问题库只给候选，不会替您决定统计口径。</p>
        <p>规则检查核对“原文位置—原子项—问题”的关联；大模型复核会对照整份模板寻找疑似遗漏或口径不符。两者都是辅助检查，最终仍需您确认。</p>
      </details>
      <p>规则检查：已将{{ result.audit.covered_slot_count }}/{{ result.audit.slot_count }}个待填位置、{{ result.audit.covered_atomic_item_count }}/{{ result.audit.atomic_item_count }}个原子项关联到问题。这里只检查结构对应，业务语义仍需复核。</p>
      <details v-if="result.audit.issues.length"><summary>查看{{ result.audit.issues.length }}条待核对事项</summary>
        <ul><li v-for="issue in result.audit.issues" :key="`${issue.target}-${issue.message}`">{{ issue.target }}：{{ issue.message }}</li></ul>
      </details>
      <button :disabled="reviewing || chat.streaming" @click="reviewWithModel">{{ reviewing ? '大模型复核中…' : '让大模型核对遗漏和匹配' }}</button>
      <section v-if="result.model_review" class="model-review">
        <p><strong>{{ result.model_review.model }}复核意见：</strong>{{ result.model_review.summary }}</p>
        <ul><li v-for="item in result.model_review.items" :key="item.question_id">{{ item.question_id }} · {{ item.status }}：{{ item.reason }}<span v-if="item.missing_metrics?.length">；疑似遗漏：{{ item.missing_metrics.join('、') }}</span></li></ul>
        <p v-if="result.model_review.missing_items?.length">整体疑似遗漏：{{ result.model_review.missing_items.join('、') }}</p>
        <p>这些是核对建议，最终以原文和人工确认的统计口径为准。</p>
      </section>
      <h3>步骤二：确认问题并逐项查询</h3>
      <p>下面的问题由模板自动整理。请检查原文、提取问题和标准问题，确认后逐项发送到中间的智能体对话框。</p>
      <p v-if="!result.questions.length">没有识别到待填项，请确认上传的是带占位符或空表格的模板，而非已生成的报告。</p>
      <ol>
        <li v-for="q in result.questions" :id="`template-question-${q.id}`" :key="q.id">
          <label :for="q.id">{{ q.id }} · 来源 {{ q.source_block }}</label>
          <div class="source-compare">
            <div><strong>模板原文</strong><p>{{ q.source_text }}</p></div>
            <div><strong>提取的问题（可以修改）</strong><textarea :id="q.id" v-model="q.question" :disabled="q.confirmed" rows="4" @input="invalidateMatch(q)" /></div>
          </div>
          <p class="requirements">统计要求：{{ q.requirements }}</p>
          <div class="library-select">
            <strong>对应标准问题</strong>
            <p>这里的百分比是文字规则分，只用于候选排序，不代表正确率。向量召回仅补充候选，不自动决定查询口径。</p>
            <p v-if="q.matchStale" class="warning">问题已修改，原匹配结果失效。请重新匹配，或选择直接询问智能体。</p>
            <p v-else-if="q.question_library_match.status !== 'candidate_found'" class="warning">
              该问题未找到可靠匹配的标准问题。以下是最相近的{{ candidates(q).length }}个候选，请核对业务口径后选择。
            </p>
            <p v-else>系统推荐以下标准问题，执行前请核对统计口径。</p>
            <label v-for="candidate in candidates(q)" :key="candidate.id" class="candidate-option" :class="{ 'candidate-incompatible': !candidate.compatible || q.matchStale }">
              <input v-model="q.selectedLibraryId" type="radio" :name="`${q.id}-candidate`" :value="candidate.id" :disabled="q.executing || q.execution || !candidate.compatible" />
              <span><strong>{{ candidate.question }}</strong><br />{{ candidate.id }} · {{ candidate.retrieval_source === 'vector' ? '向量补充候选 · ' : '' }}文字规则分 {{ Math.round(candidate.confidence * 100) }}%<br />推荐依据：{{ candidate.match_reasons.join('；') || '向量语义召回，具体口径需核对' }}</span>
            </label>
            <label class="candidate-option"><input v-model="q.selectedLibraryId" type="radio" :name="`${q.id}-candidate`" value="" :disabled="q.executing || q.execution" /><span>以上均不合适，直接询问智能体</span></label>
            <button :disabled="q.confirmed || q.executing || q.execution || q.rematching" @click="rematch(q)">{{ q.rematching ? '重新匹配中…' : '按修改后的问题重新匹配' }}</button>
          </div>
          <p v-if="!q.selectedLibraryId" class="warning">智能体回答会显示在中间对话框；完成结构化绑定前不会自动回填报告。</p>
          <details class="atomic-items">
            <summary>查看本组{{ q.atomic_item_ids.length }}个原子查询项</summary>
            <ul>
              <li v-for="item in itemsForQuestion(q)" :key="item.id">
                <strong>{{ item.id }} · {{ item.metric }}</strong>
                <span>类型：{{ item.expected_type }}；单位：{{ item.unit || '待确认' }}</span>
                <span>回填：{{ item.binding.target_type }} · {{ item.binding.slot_id }}</span>
              </li>
            </ul>
          </details>
          <label><input v-model="q.confirmed" type="checkbox" :disabled="q.executing || q.execution" />我已核对原文、问题和统计口径</label>
          <button :disabled="!canAsk(q)" @click="askAgent(q)">{{ questionButtonText(q) }}</button>
          <p v-if="q.askError" class="warning" role="alert">{{ q.askError }}</p>
          <div v-if="q.execution" class="query-result">
            <strong>查询结果：{{ q.execution.result.row_count }}行</strong>
            <table>
              <thead><tr><th v-for="column in q.execution.result.columns" :key="column">{{ columnLabel(column) }}</th></tr></thead>
              <tbody>
                <tr v-for="(row, index) in q.execution.result.rows.slice(0, 10)" :key="index">
                  <td v-for="column in q.execution.result.columns" :key="column">{{ row[column] }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="q.execution.result.row_count > 10">这里只预览前10行，完整结果已经保存用于回填。</p>
          </div>
        </li>
      </ol>
      <h3>正文数值回填确认（非标准模板试用）</h3>
      <p>对照中间智能体回答，逐项填写纯数值和来源。这里确认的是“答案对应哪个待填位置”；不改动原模板。当前仅支持正文数值，表格和图表仍需后续绑定。</p>
      <details><summary>查看并确认{{ scalarSlots.length }}个正文待填位置</summary>
        <div v-for="slot in scalarSlots" :key="slot.id" class="binding-card">
          <strong>{{ slot.id }} · {{ itemForSlot(slot.id)?.metric || '待核对指标' }}</strong>
          <p>模板原文：{{ slot.context }}</p>
          <p>占位符：{{ slot.text }}；原文块：{{ slot.block_id }}；关联问题：{{ questionForSlot(slot.id)?.id || '未找到' }}</p>
          <label>填入数值（不含单位和逗号）<input v-model="bindingDrafts[slot.id].value" type="text" inputmode="decimal" @input="invalidateBinding(slot.id)" /></label>
          <label>答案来源（例如“Q1智能体回答：耕地流入”）<input v-model="bindingDrafts[slot.id].sourceNote" type="text" @input="invalidateBinding(slot.id)" /></label>
          <button :disabled="!bindingDrafts[slot.id].value.trim() || !bindingDrafts[slot.id].sourceNote.trim() || bindingSaving === slot.id" @click="saveBinding(slot)">{{ bindingSaving === slot.id ? '保存中…' : '确认并保存此处回填' }}</button>
          <span v-if="confirmedBindings[slot.id]" class="binding-confirmed">已确认：{{ confirmedBindings[slot.id].value }}</span>
        </div>
      </details>
      <p v-if="Object.keys(confirmedBindings).length">已确认{{ Object.keys(confirmedBindings).length }}/{{ scalarSlots.length }}个正文待填位置。</p>
      <h3>固定地区行表格回填确认</h3>
      <p>仅展示可安全按原有行名定位的简单表格。请对照查询结果填写每个地区和栏目对应的数值；原有行名不会被替换。没有权威数据的栏目请留空。</p>
      <details v-for="option in result.fixed_tables || []" :key="option.slot_id">
        <summary>{{ option.title }} · {{ option.rows.length }}个固定行、{{ option.columns.length }}个数值列</summary>
        <p v-if="option.row_header.includes('县级') && option.rows.some(row => row.key.endsWith('市'))" class="warning">模板表头写“县级行政区”，但行内包含市名；请在正式使用前核对行政层级。</p>
        <div class="query-result">
          <table>
            <thead><tr><th>{{ option.row_header }}</th><th v-for="column in option.columns" :key="column">{{ column }}</th></tr></thead>
            <tbody><tr v-for="row in option.rows" :key="row.key">
              <th>{{ row.key }}</th>
              <td v-for="cell in row.cells" :key="cell.id">
                <input v-if="cell.editable" v-model="tableDrafts[option.slot_id].cells[cell.id]" type="text" inputmode="decimal" :aria-label="`${row.key} ${cell.column}`" @input="invalidateTableBinding(option.slot_id)" />
                <span v-else>原样保留</span>
              </td>
            </tr></tbody>
          </table>
        </div>
        <label class="table-source">本表数值来源（例如“Q5智能体回答，2026年第一期，单位亩”）
          <input v-model="tableDrafts[option.slot_id].sourceNote" type="text" @input="invalidateTableBinding(option.slot_id)" />
        </label>
        <button :disabled="tableSaving === option.slot_id || !tableDrafts[option.slot_id].sourceNote.trim() || !filledTableCells(option).length" @click="saveTableBinding(option)">{{ tableSaving === option.slot_id ? '保存中…' : '确认并保存本表已填写单元格' }}</button>
        <span v-if="confirmedTableBindings[option.slot_id]" class="binding-confirmed">已确认{{ Object.keys(confirmedTableBindings[option.slot_id].cells).length }}格</span>
      </details>
      <p v-if="!(result.fixed_tables || []).length">本模板没有可按固定地区行安全回填的简单表格；排名表和多层合并表暂不在此处绑定。</p>
      <button :disabled="!canGenerateReview || generating" @click="generateReview">{{ generating ? '正在生成核验版…' : '生成已确认数值的Word/PDF核验版' }}</button>
      <p v-if="error" class="warning" role="alert">{{ error }}</p>
      <p v-if="generated?.review_only">核验版已生成：<a :href="generated.word.url" download>{{ generated.word.filename }}</a><template v-if="generated.pdf"> · <a :href="generated.pdf.url" download>{{ generated.pdf.filename }}</a></template><span v-else>；PDF未生成：{{ generated.pdf_error }}</span></p>
      <p>核验版仅填已确认的正文和简单表格数值；其他表格保持原样，图表暂不绑定，不能作为正式业务报告。</p>
      <h3>步骤三：生成报告</h3>
      <p>每个问题都在智能体对话中回答并保存结果后，系统使用同一批结果回填正文和表格，并生成饼图、柱状图及Word/PDF文件。生成过程和下载文件也会显示在中间的智能体对话框。</p>
      <p v-if="hasUnboundAgentAnswers" class="warning">存在直接交给智能体的问题，其回答尚未绑定到模板槽位，因此暂不能生成最终报告。这正是本轮需要检查的输出。</p>
      <button :disabled="!allExecuted || generating || generated" @click="generate">{{ generationButtonText }}</button>
      </details>
    </template>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { chat } from '../stores/chat'

const result = ref(null)
const busy = ref(false)
const error = ref('')
const generating = ref(false)
const generated = ref(null)
const reviewing = ref(false)
const bindingDrafts = ref({})
const confirmedBindings = ref({})
const bindingSaving = ref('')
const tableDrafts = ref({})
const confirmedTableBindings = ref({})
const tableSaving = ref('')
const autoBusy = ref(false)
const autoError = ref('')
const referenceBindings = ref({})
const pendingFreeQuestion = ref(null)
const unmatchedQuestions = computed(() => result.value?.questions.filter(
  q => !q.execution,
) || [])
const queriedCount = computed(() => result.value?.questions.filter(q => q.execution).length || 0)
const pendingItems = computed(() => {
  const summary = generated.value?.summary
  if (!summary) return []
  const items = [...(summary.pending_items || []), ...(summary.pending_scalars || [])]
  if (summary.pending_tables) items.push(`${summary.pending_tables}张表格`)
  return items
})

function templateName(kind) {
  return { client: '快报模板（删除统计数值）', library: '标准问题库示例模板', brother: '监测快报模板（待填版）' }[kind] || '已核验模板'
}

function safeAutoCandidate(question) {
  const best = question.question_library_match?.candidates?.[0]
  return question.question_library_match?.status === 'candidate_found'
    && best?.compatible && best.confidence >= 0.8
    && best.match_reasons.some(reason => reason.includes('标准问题文本相互包含') || reason.includes('命中别名'))
}

const scalarSlots = computed(() => (
  result.value?.template.slots.filter(slot => slot.kind === 'placeholder') || []
))
const canGenerateReview = computed(() => {
  const confirmed = Object.values(confirmedBindings.value)
  const tables = Object.values(confirmedTableBindings.value)
  return (confirmed.length + tables.length) > 0 && confirmed.every(binding => (
    bindingDrafts.value[binding.slot_id]?.value === binding.value
    && bindingDrafts.value[binding.slot_id]?.sourceNote === binding.source_note
  )) && tables.every(binding => (
    tableDrafts.value[binding.slot_id]?.sourceNote === binding.source_note
    && JSON.stringify(Object.fromEntries(filledTableCells(
      result.value.fixed_tables.find(option => option.slot_id === binding.slot_id),
    ).map(cell => [cell.cell_id, cell.value]))) === JSON.stringify(binding.cells)
  ))
})

const allExecuted = computed(() => (
  Boolean(result.value?.questions.length)
  && result.value.questions.every(question => Boolean(question.execution))
))
const hasUnboundAgentAnswers = computed(() => (
  Boolean(result.value?.questions.some(question => question.directAsked && !question.execution))
))
const generationButtonText = computed(() => {
  if (generated.value) return '报告已发送到智能体'
  if (generating.value) return '智能体正在生成…'
  return '发送到智能体并生成Word和PDF'
})

async function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  result.value = null
  generated.value = null
  autoError.value = ''
  confirmedBindings.value = {}
  bindingDrafts.value = {}
  confirmedTableBindings.value = {}
  tableDrafts.value = {}
  referenceBindings.value = {}
  pendingFreeQuestion.value = null
  error.value = ''
  if (!file.name.toLowerCase().endsWith('.docx') || file.size > 10 * 1024 * 1024) {
    error.value = '请选择不超过10MB的DOCX模板。'
    return
  }
  busy.value = true
  try {
    const response = await fetch(`/api/report-templates/parse?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '解析失败')
    data.questions = data.questions.map(question => ({
      ...question,
      confirmed: false,
      executing: false,
      execution: null,
      directAsked: false,
      askError: '',
      rematching: false,
      matchStale: false,
      selectedLibraryId: question.question_library_match?.status === 'candidate_found'
        && question.question_library_match?.candidates?.[0]?.compatible
        ? question.question_library_match.candidates[0].id : '',
    }))
    bindingDrafts.value = Object.fromEntries(
      data.template.slots.filter(slot => slot.kind === 'placeholder')
        .map(slot => [slot.id, { value: '', sourceNote: '' }]),
    )
    tableDrafts.value = Object.fromEntries((data.fixed_tables || []).map(option => [
      option.slot_id,
      { sourceNote: '', cells: Object.fromEntries(
        option.rows.flatMap(row => row.cells.filter(cell => cell.editable).map(cell => [cell.id, ''])),
      ) },
    ]))
    result.value = data
    busy.value = false
    if (data.supported_template_kind) await autoGenerateSupported()
    else await autoExecuteMatched()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function autoGenerateSupported() {
  if (!result.value?.supported_template_kind || autoBusy.value) return
  autoBusy.value = true
  autoError.value = ''
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/auto-generate-supported`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: chat.currentId || '' }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.detail || `自动生成失败（HTTP ${response.status}）`)
    generated.value = data
    if (chat.currentId) await chat.refreshMessages()
  } catch (e) {
    autoError.value = e.message
  } finally {
    autoBusy.value = false
  }
}

async function autoExecuteMatched() {
  for (const question of result.value.questions) {
    if (!safeAutoCandidate(question)) continue
    question.selectedLibraryId = question.question_library_match.candidates[0].id
    question.confirmed = true
    await askAgent(question)
  }
}

async function chooseCandidate(question, libraryId) {
  question.selectedLibraryId = libraryId
  question.confirmed = true
  question.directAsked = false
  await askAgent(question)
}

async function chooseFree(question) {
  question.selectedLibraryId = ''
  question.confirmed = true
  if (await askAgent(question)) pendingFreeQuestion.value = question
}

async function captureFree(question) {
  if (!chat.currentId || chat.streaming) return
  question.captureNotice = '正在核对答案与位置…'
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/questions/${question.id}/capture-free`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: chat.currentId }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.detail || '答案核对失败')
    question.captureNotice = data.notice
    if (data.status === 'bound_reference') referenceBindings.value[data.slot_id] = data
  } catch (e) {
    question.captureNotice = e.message
  }
}

watch(() => chat.streaming, (streaming, previous) => {
  if (previous && !streaming && pendingFreeQuestion.value) {
    const question = pendingFreeQuestion.value
    pendingFreeQuestion.value = null
    if (!chat.error) captureFree(question)
  }
})

async function generatePending() {
  if (!result.value || generating.value) return
  generating.value = true
  error.value = ''
  try {
    const boundIds = [...new Set([...Object.keys(confirmedBindings.value), ...Object.keys(referenceBindings.value)])]
    const tableIds = Object.keys(confirmedTableBindings.value)
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: chat.currentId || '',
        request_text: '请生成核验版，未确定的位置标为待核实。',
        generic_mode: true,
        include_pdf: false,
        confirmed_slot_ids: boundIds,
        confirmed_table_slot_ids: tableIds,
      }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.detail || '核验版生成失败')
    generated.value = data
    if (chat.currentId) await chat.refreshMessages()
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

function download() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(result.value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = '模板解析与问数.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function candidates(question) {
  return question.matchStale ? [] : question.question_library_match?.candidates || []
}

function invalidateMatch(question) {
  question.matchStale = true
  question.selectedLibraryId = ''
}

async function reviewWithModel() {
  reviewing.value = true
  error.value = ''
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: chat.currentId,
        questions: Object.fromEntries(result.value.questions.map(q => [q.id, q.question])),
      }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '复核失败')
    result.value.model_review = data
  } catch (e) {
    error.value = e.message
  } finally {
    reviewing.value = false
  }
}

function canAsk(question) {
  return question.confirmed && !question.executing
    && !question.execution && (!question.directAsked || Boolean(chat.error)) && !chat.streaming
}

async function rematch(question) {
  question.rematching = true
  error.value = ''
  try {
    const response = await fetch(
      `/api/report-templates/${result.value.workflow_id}/questions/${question.id}/rematch`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.question }) },
    )
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '重新匹配失败')
    question.question_library_match = data
    question.matchStale = false
    question.selectedLibraryId = ''
  } catch (e) {
    error.value = e.message
  } finally {
    question.rematching = false
  }
}

function questionButtonText(question) {
  if (question.execution) return '问数完成'
  if (question.directAsked && chat.error) return '重试询问智能体'
  if (question.directAsked) return '已发送智能体（待绑定）'
  if (question.executing) return '智能体查询中…'
  return question.selectedLibraryId ? '发送到智能体' : '直接询问智能体'
}

async function askAgent(question) {
  if (!canAsk(question)) return false
  question.askError = ''
  if (!question.selectedLibraryId) {
    if (!chat.sendMessage(question.question, {
      source: 'template_free',
      workflow_id: result.value.workflow_id,
      question_id: question.id,
    })) {
      question.askError = '智能体连接尚未就绪，请稍后重试。'
      return false
    }
    question.directAsked = true
    return true
  }
  question.executing = true
  try {
    const response = await fetch(
      `/api/report-templates/${result.value.workflow_id}/questions/${question.id}/execute`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          library_id: question.selectedLibraryId,
          question: question.question,
          conversation_id: chat.currentId,
        }),
      },
    )
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '查询失败')
    question.execution = data
    for (const [slotId, value] of Object.entries(data.auto_bindings || {})) {
      referenceBindings.value[slotId] = { slot_id: slotId, value, notice: '标准问题结果自动绑定' }
    }
    await chat.refreshMessages()
    return true
  } catch (e) {
    question.askError = e.message
    return false
  } finally {
    question.executing = false
  }
}

async function generate() {
  if (!allExecuted.value) return
  generating.value = true
  generated.value = null
  error.value = ''
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: chat.currentId,
        request_text: '请根据以上问数结果生成Word和PDF快报。',
      }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '报告生成失败')
    generated.value = data
    await chat.refreshMessages()
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

function itemsForQuestion(question) {
  const ids = new Set(question.atomic_item_ids || [])
  return result.value.atomic_items.filter(item => ids.has(item.id))
}

function itemForSlot(slotId) {
  return result.value.atomic_items.find(item => item.binding.slot_id === slotId)
}

function questionForSlot(slotId) {
  return result.value.questions.find(question => question.slot_ids.includes(slotId))
}

function invalidateBinding(slotId) {
  delete confirmedBindings.value[slotId]
}

function filledTableCells(option) {
  if (!option) return []
  const draft = tableDrafts.value[option.slot_id]
  return option.rows.flatMap(row => row.cells)
    .filter(cell => cell.editable && draft.cells[cell.id]?.trim())
    .map(cell => ({ cell_id: cell.id, value: draft.cells[cell.id].trim() }))
}

function invalidateTableBinding(slotId) {
  delete confirmedTableBindings.value[slotId]
}

async function saveTableBinding(option) {
  const question = questionForSlot(option.slot_id)
  if (!question) {
    error.value = `${option.slot_id}没有对应问题，暂不能绑定。`
    return
  }
  tableSaving.value = option.slot_id
  error.value = ''
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/bindings/fixed-table`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        slot_id: option.slot_id,
        source_question_id: question.id,
        source_note: tableDrafts.value[option.slot_id].sourceNote,
        cells: filledTableCells(option),
      }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '表格绑定保存失败')
    tableDrafts.value[option.slot_id].sourceNote = data.source_note
    for (const [cellId, value] of Object.entries(data.cells)) {
      tableDrafts.value[option.slot_id].cells[cellId] = value
    }
    confirmedTableBindings.value[option.slot_id] = data
  } catch (e) {
    error.value = e.message
  } finally {
    tableSaving.value = ''
  }
}

async function saveBinding(slot) {
  const question = questionForSlot(slot.id)
  if (!question) {
    error.value = `${slot.id}没有对应问题，暂不能绑定。`
    return
  }
  bindingSaving.value = slot.id
  error.value = ''
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/bindings/scalar`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        slot_id: slot.id,
        value: bindingDrafts.value[slot.id].value,
        source_question_id: question.id,
        source_note: bindingDrafts.value[slot.id].sourceNote,
      }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '绑定保存失败')
    bindingDrafts.value[slot.id] = { value: data.value, sourceNote: data.source_note }
    confirmedBindings.value[slot.id] = data
  } catch (e) {
    error.value = e.message
  } finally {
    bindingSaving.value = ''
  }
}

async function generateReview() {
  if (!canGenerateReview.value) return
  generating.value = true
  error.value = ''
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: chat.currentId,
        request_text: '请按已确认的正文和表格数值生成Word和PDF核验版。',
        binding_mode: true,
        confirmed_slot_ids: Object.keys(confirmedBindings.value),
        confirmed_table_slot_ids: Object.keys(confirmedTableBindings.value),
      }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.detail || `核验版生成失败（HTTP ${response.status}），请查看后端日志。`)
    generated.value = data
    await chat.refreshMessages()
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

function formatTime(value) {
  if (!value) return '未知'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function columnLabel(value) {
  return {
    n: '图斑数量',
    area_mu: '面积（亩）',
    TBLX: '图斑类型',
    region: '县（市、区）',
  }[value] || value
}
</script>

<style scoped>
.template-workflow { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 16px 24px; }
.workflow-summary { margin: 12px 0; padding: 10px 12px; border: 1px solid #d8dde5; border-radius: 8px; background: #f7fafc; }
.workflow-summary p { margin: 6px 0; }
.workflow-summary ul { padding-left: 18px; }
.workflow-summary li { margin: 6px 0; overflow-wrap: anywhere; }
.workflow-summary li button { margin-left: 8px; }
.question-choice { padding: 8px 0; border-top: 1px solid #d8dde5; }
.question-choice > summary { cursor: pointer; font-size: 13px; line-height: 1.5; }
.question-choice > button { display: block; width: 100%; margin: 6px 0; text-align: left; white-space: normal; }
.advanced-inspection { margin-top: 14px; border-top: 1px solid #d8dde5; padding-top: 12px; }
.advanced-inspection > summary { cursor: pointer; font-size: 14px; color: #43566e; }
h2 { font-size: 18px; }
h3 { font-size: 16px; }
p, li { font-size: 14px; line-height: 1.7; }
textarea { display: block; width: 100%; margin: 8px 0; padding: 8px; font: inherit; }
.source-compare { display: grid; grid-template-columns: 1fr; gap: 8px; margin: 10px 0; }
.source-compare > div { padding: 10px; background: #f7f8fa; border: 1px solid #ddd; border-radius: 6px; }
.source-compare p { margin: 8px 0 0; white-space: pre-wrap; }
.requirements { color: #555; }
.library-select { display: block; margin: 10px 0; }
.candidate-option { display: flex; gap: 8px; align-items: flex-start; margin: 7px 0; padding: 8px; border: 1px solid #d8dde5; border-radius: 6px; font-size: 13px; line-height: 1.5; }
.candidate-option input { margin-top: 3px; flex: 0 0 auto; }
.candidate-option span { min-width: 0; overflow-wrap: anywhere; }
.candidate-incompatible { opacity: .58; }
.template-workflow input[type="file"] { max-width: 100%; }
.atomic-items { margin: 8px 0; }
.atomic-items ul { padding-left: 20px; }
.atomic-items li { margin: 6px 0; }
.atomic-items span { display: block; color: #666; font-size: 13px; }
button { padding: 6px 12px; margin: 4px 0; cursor: pointer; }
button:disabled { cursor: default; opacity: .5; }
.query-result { margin: 10px 0; overflow-x: auto; }
.query-result table { width: 100%; border-collapse: collapse; margin-top: 8px; }
.query-result th, .query-result td { border: 1px solid #d8dde5; padding: 6px 8px; text-align: center; white-space: nowrap; }
.query-result th { background: #eef4fa; }
.warning { color: #9a5a00; }
.model-review { margin: 8px 0; padding: 8px 10px; background: #f2f6fb; border-radius: 6px; }
.model-review ul { padding-left: 18px; }
.binding-card { margin: 10px 0; padding: 10px; border: 1px solid #d8dde5; border-radius: 6px; }
.binding-card p { margin: 5px 0; }
.binding-card label { display: block; margin: 8px 0; font-size: 13px; }
.binding-card input { box-sizing: border-box; display: block; width: 100%; margin-top: 4px; padding: 6px; }
.binding-confirmed { color: #26703c; margin-left: 8px; }
.table-source { display: block; margin: 8px 0; font-size: 13px; }
.table-source input { box-sizing: border-box; display: block; width: 100%; margin-top: 4px; padding: 6px; }
.query-result td input { box-sizing: border-box; width: 86px; padding: 4px; }
.generated-files { margin: 10px 0; padding: 10px; background: #f3f8f3; border: 1px solid #cfe3cf; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 200px; overflow: auto; }
</style>
