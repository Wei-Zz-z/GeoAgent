// 图表渲染公共工具：配色与数值缩写格式（饼图/柱状图/折线图共用）

export const CHART_PALETTE = [
  '#4e79a7',
  '#f28e2b',
  '#59a14f',
  '#e15759',
  '#76b7b2',
  '#edc948',
  '#b07aa1',
  '#ff9da7',
  '#9c755f',
  '#bab0ac',
]

export function color(i) {
  return CHART_PALETTE[i % CHART_PALETTE.length]
}

export function fmt(v) {
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  if (Number.isInteger(v)) return String(v)
  return v.toFixed(2)
}
