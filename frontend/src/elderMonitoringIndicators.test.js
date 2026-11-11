import test from 'node:test'
import assert from 'node:assert/strict'
import { elderMonitoringIndicators, monitoringExportConclusions, wrapMeasuredText, conclusionLayout } from './elderMonitoringIndicators.js'

function result(overrides = {}) {
  return {
    travel_mode: 'walking', statistics: [{ minutes: 15 }],
    monitoring_indicators: [
      { id: 'community_elder_15min_coverage', value: null, status: 'missing_data', reason: '缺少居住用地，设施范围待核实。' },
      { id: 'single_facility_residential_15min', value: 0, status: 'trial', reason: '本设施概略范围试算。' },
    ],
    ...overrides,
  }
}

test('真实零值显示0.00%，缺数据仍是未计算', () => {
  const view = elderMonitoringIndicators(result())
  assert.equal(view.official.display, '未计算')
  assert.equal(view.official.value, null)
  assert.equal(view.trial.display, '0.00%')
  assert.equal(view.trial.value, 0)
})

test('不从行政区面积或人口百分比填充养老监测值', () => {
  const view = elderMonitoringIndicators(result({ statistics: [{ minutes: 15, district_coverage_percent: 88, population_coverage_percent: 99 }] }))
  assert.equal(view.official.value, null)
  assert.match(view.official.reason, /居住用地/)
})

test('驾车和没有15分钟结果时不采用本设施试算数值', () => {
  for (const data of [result({ travel_mode: 'driving' }), result({ statistics: [{ minutes: 10 }] }), result({ statistics: [] })]) {
    const view = elderMonitoringIndicators(data)
    assert.equal(view.trial.value, null)
    assert.match(view.trial.reason, /15分钟步行/)
    assert.ok(monitoringExportConclusions(data).every(line => !line.includes('0.00%')))
  }
})

test('旧记录和非数值兼容，不把空值转成零', () => {
  assert.equal(elderMonitoringIndicators().official.display, '未计算')
  assert.match(elderMonitoringIndicators().official.reason, /15分钟步行/)
  const old = elderMonitoringIndicators({ travel_mode: 'walking', statistics: [{ minutes: 15 }] })
  assert.equal(old.available, false)
  assert.match(old.official.reason, /历史记录/)
  const data = result()
  for (const value of [null, NaN, Infinity, '42', -1, 100.01]) {
    data.monitoring_indicators[1].value = value
    assert.equal(elderMonitoringIndicators(data).trial.display, '未计算')
  }
})

test('状态冲突时不显示正式比例，试算也须有trial状态', () => {
  const data = result()
  data.monitoring_indicators[0].value = 50
  for (const status of ['missing_data', 'scope_incomplete', 'trial']) {
    data.monitoring_indicators[0].status = status
    assert.equal(elderMonitoringIndicators(data).official.value, null)
  }
  data.monitoring_indicators[1].status = 'missing_data'
  assert.equal(elderMonitoringIndicators(data).trial.value, null)
})

test('网页与导出沿用同一缺项和本设施试算口径', () => {
  const data = result()
  const view = elderMonitoringIndicators(data)
  const lines = monitoringExportConclusions(data)
  assert.ok(lines[0].includes(view.official.reason))
  assert.ok(lines[1].includes(view.trial.display))
  assert.match(lines[1], /不代表全区监测值/)
})

test('导出结论按测量宽度换行且保持文本完整', () => {
  const text = '覆盖率未计算：缺少居住用地，不能用人口占比代替。'
  const lines = wrapMeasuredText(text, value => [...value].length * 15, 90)
  assert.equal(lines.join(''), text)
  assert.ok(lines.every(line => [...line].length * 15 <= 90))
})

test('长缺项会增加导出高度，短结论保留方形地图原有高度', () => {
  const measureWidth = text => [...text].length * 15
  const short = conclusionLayout(['短结论'], measureWidth, 388, 746)
  assert.equal(short.pageHeight, 746)
  const statement = '缺少居住用地数据，设施类别和完整性还需核实。'.repeat(15)
  const long = conclusionLayout([statement], measureWidth, 388, 746)
  assert.ok(long.pageHeight > 746)
  assert.equal(long.lines[0].join(''), statement)
  const lastLineY = 388 + 30 + (long.lines[0].length - 1) * 22
  assert.ok(long.pageHeight - 20 > lastLineY)
})
