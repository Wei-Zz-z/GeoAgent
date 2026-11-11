// 仅在展示层换算，空间分析仍使用平方米。
export function areaMu(squareMetres, fallbackMu = null) {
  const value = squareMetres == null ? fallbackMu : squareMetres * 0.0015
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '未记录'
}
