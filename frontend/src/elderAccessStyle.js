import { Circle as CircleStyle, Fill, Stroke, Style, Text } from 'ol/style.js'

// 默认单档；叠加时仅放开多边形，道路与未覆盖面仍按当前档显示。
export function elderAccessStyle(feature, minutes, color, showUncovered = false, selectedBoundary = '', showAllTimes = false) {
  const kind = feature.get('kind')
  if (kind === 'village_boundary') return feature.get('code') === selectedBoundary ? new Style({ stroke: new Stroke({ color: '#7c3aed', width: 3, lineDash: [7, 3] }), fill: new Fill({ color: '#7c3aed12' }), zIndex: 12 }) : undefined
  if (kind === 'facility') return new Style({
    image: new CircleStyle({ radius: 7, fill: new Fill({ color: '#dc2626' }), stroke: new Stroke({ color: '#fff', width: 2 }) }),
    text: new Text({ text: feature.get('name'), offsetY: -18, font: 'bold 12px sans-serif', fill: new Fill({ color: '#991b1b' }), stroke: new Stroke({ color: '#fff', width: 4 }) }),
    zIndex: 20,
  })
  if (kind === 'connector') return new Style({ stroke: new Stroke({ color: '#dc2626', width: 2, lineDash: [5, 5] }), zIndex: 19 })
  if (kind === 'study_area') return showUncovered ? new Style({ stroke: new Stroke({ color: '#475569', width: 2, lineDash: [8, 4] }), zIndex: 2 }) : undefined
  if (feature.get('minutes') !== minutes && !(showAllTimes && kind === 'area')) return undefined
  if (kind === 'uncovered') return showUncovered ? new Style({ stroke: new Stroke({ color: '#64748b', width: 1 }), fill: new Fill({ color: '#64748b30' }), zIndex: -1 }) : undefined
  if (kind !== 'road' && kind !== 'area') return undefined
  return new Style({
    stroke: new Stroke({ color, width: kind === 'road' ? 2.5 : 1.5 }),
    fill: kind === 'area' ? new Fill({ color: `${color}75` }) : undefined,
    // 大范围在下、小范围在上，避免长时段完全盖住短时段。
    zIndex: kind === 'road' ? 10 : showAllTimes ? 1 + 1 / (1 + feature.get('minutes')) : 0,
  })
}
