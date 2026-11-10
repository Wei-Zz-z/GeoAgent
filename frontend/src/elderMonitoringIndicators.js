// 监测口径与单设施试算共用展示规则，不从人口或行政区面积占比补值。
const OFFICIAL_ID = 'community_elder_15min_coverage'
const TRIAL_ID = 'single_facility_residential_15min'
const OFFICIAL_NAME = '社区养老设施步行15分钟覆盖率'
const TRIAL_NAME = '本设施15分钟居住用地覆盖率试算'

function indicatorView(item, name, eligible, allowedStatus, fallbackReason) {
  const valid = allowedStatus.includes(item?.status) && typeof item?.value === 'number' &&
    Number.isFinite(item.value) && item.value >= 0 && item.value <= 100
  const value = eligible && valid
    ? item.value : null
  return {
    ...item,
    name,
    value,
    display: value == null ? '未计算' : value.toFixed(2) + '%',
    reason: !eligible ? '本轮没有15分钟步行结果，未计算该指标。'
      : item?.value != null && !valid ? '指标状态或数值不一致，未采用该值，请重新分析。'
        : (item?.reason || fallbackReason),
  }
}

export function elderMonitoringIndicators(data = {}) {
  const items = Array.isArray(data?.monitoring_indicators) ? data.monitoring_indicators : []
  const eligible = data?.travel_mode === 'walking' &&
    (data?.statistics || []).some(row => row.minutes === 15)
  const historicReason = '此历史记录未保存监测指标，请重新分析。'
  const officialItem = items.find(item => item.id === OFFICIAL_ID)
  const trialItem = items.find(item => item.id === TRIAL_ID)
  const fallback = items.length ? '指标尚未计算，请查看数据缺项。' : historicReason
  return {
    available: items.length > 0,
    official: indicatorView(officialItem, OFFICIAL_NAME, eligible, ['computed'], fallback),
    trial: indicatorView(trialItem, TRIAL_NAME, eligible, ['trial'], fallback),
  }
}

export function monitoringExportConclusions(data) {
  const { official, trial } = elderMonitoringIndicators(data)
  const lines = [official.value == null
    ? `${official.name}未计算：${official.reason}`
    : `${official.name}为${official.display}。`]
  if (trial.value != null) {
    lines.push(`本设施15分钟步行居住用地覆盖试算为${trial.display}，不代表全区监测值。`)
  }
  return lines
}

// 先按实际字体测量，再用同一分行结果绘制，防止统计结论超出导出画布。
export function wrapMeasuredText(text, measureWidth, maxWidth) {
  const lines = []
  let line = ''
  for (const character of String(text)) {
    if (line && measureWidth(line + character) > maxWidth) {
      lines.push(line)
      line = ''
    }
    line += character
  }
  if (line) lines.push(line)
  return lines
}

export function conclusionLayout(statements, measureWidth, top, minimumHeight) {
  const lines = statements.map(text => wrapMeasuredText(text, measureWidth, 290))
  const height = lines.reduce((total, item) => total + 30 + Math.max(0, item.length - 1) * 22, 0)
  return { lines, pageHeight: Math.max(minimumHeight, top + height + 64) }
}
