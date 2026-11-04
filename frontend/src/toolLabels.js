// 工具英文标识 → 前端展示用中文名（工具调用名保持英文以便 LLM/API 调用）。
const TOOL_LABELS = {
  search_elder_facilities: '查找养老设施',
  analyze_elder_accessibility: '计算设施步行可达范围',
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
  task: '委派子任务',
  list_skills: '技能列表',
  load_skill: '加载技能',
  compact: '压缩上下文',
}

export function toolLabel(name) {
  return TOOL_LABELS[name] || name
}
