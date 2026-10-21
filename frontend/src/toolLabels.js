// 工具英文标识 → 前端展示用中文名（工具调用名保持英文以便 LLM/API 调用）。
const TOOL_LABELS = {
  list_tables: '查看表清单',
  describe_table: '查看表结构',
  run_sql: '执行 SQL 查询',
  summarize_by_type: '图斑类型汇总',
  fragment_stats: '细碎图斑统计',
  farmland_flow_summary: '耕地流向汇总',
  construction_change_summary: '建设用地变化汇总',
  top_conversions: '转换类型排行',
  generate_briefing: '生成监测快报',
  make_chart: '生成统计图表',
  // 预警分析
  warning_yongnong_polygons: '永久基本农田预警',
  warning_redline_polygons: '生态保护红线预警',
  // 标准问题库与字段知识检索
  search_question_library: '检索标准问题库',
  search_schema_knowledge: '检索字段知识',
  // 养老可达性分析
  describe_dataset: '查看可达性数据集',
  build_demand_grid: '生成需求网格',
  nearest_facility: '最近机构分析',
  isochrone: '步行等时圈',
  e2sca_analysis: '可达性评估',
  supply_demand_summary: '供需匹配汇总',
  task: '委派子任务',
  list_skills: '技能列表',
  load_skill: '加载技能',
  compact: '压缩上下文',
}

export function toolLabel(name) {
  // 未登记的工具统一用中文兜底，卡片上不出现英文调用名；
  // 新增工具时请在 TOOL_LABELS 登记中文名（backend/tests/test_tool_labels.py 会校验）。
  return TOOL_LABELS[name] || '工具调用'
}
