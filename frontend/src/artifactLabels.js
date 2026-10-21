// 卡片标题（artifact.name）的中文名映射。
// 后端的 artifact.name 是给程序用的标识符（英文，报告链路会按它取数），
// 因此不改后端标识符，只在这里做展示翻译：
//   1) 名称本身含汉字（如预警明细“疑似占用永农明细（面积前 20）”）→ 原样使用；
//   2) 已登记的英文标识符 → 用下面的中文名；
//   3) 未登记的英文标识符 → 按卡片类型兜底，保证卡片上不出现英文。
const ARTIFACT_LABELS = {
  // 受控 SQL 层
  query_result: '查询结果',
  tables: '数据库表清单',
  table_columns: '表结构',
  // 内置工具
  skills: '可用技能',
  // 确定性统计工具
  total_summary: '总体汇总',
  by_type_summary: '图斑类型汇总',
  fragment_stats: '细碎图斑统计',
  farmland_metrics: '耕地变化指标',
  farmland_outflow_by_category: '耕地流出去向',
  farmland_inflow_source: '耕地流入来源',
  construction_metrics: '建设用地变化指标',
  construction_by_region: '分县建设用地变化',
  construction_source: '建设用地来源',
  top_conversions: '转换类型排行',
  // 养老可达性分析
  dataset_info: '数据集信息',
  coverage_summary: '覆盖情况汇总',
  isochrone_areas: '等时圈面积',
  top_townships: '街道可达性排行',
  demand_grid: '需求网格分布',
  nearest_facility: '最近机构分布',
  isochrones: '步行等时圈分布',
  e2sca_grid: '可达性网格分布',
  e2sca_townships: '街道可达性分布',
  e2sca_districts: '区县可达性分布',
  supply_demand: '供需匹配分布',
  supply_demand_gaps: '供需缺口（街道）',
  // 快报
  briefing_summary: '快报关键指标',
  briefing: '监测快报',
}

const KIND_FALLBACK = {
  table: '数据表',
  geojson: '空间分布图',
  chart: '统计图',
  file: '文件',
}

export function artifactLabel(name, kind) {
  const text = String(name || '').trim()
  if (text && /[\u4e00-\u9fff]/.test(text)) return text
  if (text && ARTIFACT_LABELS[text]) return ARTIFACT_LABELS[text]
  return KIND_FALLBACK[kind] || '查询结果'
}