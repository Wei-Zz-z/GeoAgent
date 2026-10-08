export const countyColors = ['#4472c4', '#ed7d31', '#70ad47', '#ffc000', '#9966cc', '#36a2a8', '#c0504d', '#6b7280']

export function countySeries(values, limit = 5) {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1])
  const head = entries.slice(0, limit)
  if (entries.length > limit) head.push(['其余地类', entries.slice(limit).reduce((sum, item) => sum + item[1], 0)])
  return head
}

export function countyColor(label, labels) {
  return label === '其余地类' ? '#9ca3af' : countyColors[labels.indexOf(label) % countyColors.length]
}
