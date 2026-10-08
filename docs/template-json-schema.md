# 快报模板解析 JSON 字段草案

## 目标

本结构负责保存模板原文、待查询指标和回填位置。问题转 SQL 与数据库执行由查询模块负责，模板模块只提供稳定输入并接收按 `field_id` 返回的结果。

## 顶层结构

| 字段 | 含义 |
|---|---|
| `template` | 原始模板结构、章节、槽位、图片和图表锚点 |
| `atomic_items` | 每个独立查询值或表格对应的原子查询项 |
| `questions` | 面向用户审核的业务问题组，一个问题组可以包含多个原子项 |
| `stage` | 当前工作流阶段 |

## 模板结构

- `sections`：章节标题、级别、标题块和定位。
- `blocks`：按原顺序保存段落和表格。
- `slots`：逻辑槽位。段落占位符单独记录，整张表作为一个逻辑区域。
- `images`：图片关系、类型、尺寸和正文位置。
- `chart_anchors`：图题、现有图片和未来图表插入位置。
- `package_parts`：页眉、页脚和媒体资源清单。

## 原子查询项

标量项示例：

```json
{
  "id": "field-001",
  "kind": "scalar",
  "section_id": "section-1",
  "source_block": "b7",
  "source_text": "2024年上半年全省耕地流入***万亩……",
  "source_fragment": "2024年上半年全省耕地流入***万亩",
  "metric": "耕地流入面积",
  "scope": "全省",
  "period": "2026年第一期",
  "unit": "万亩",
  "expected_type": "number",
  "query_question": "查询2026年第一期耕地流入面积",
  "binding": {
    "target_type": "paragraph_placeholder",
    "slot_id": "b7:s0",
    "locator": {
      "part": "word/document.xml",
      "block_id": "b7",
      "start": 16,
      "end": 19
    }
  },
  "question_library_match": {
    "status": "unmatched",
    "matched_question_id": null,
    "confidence": null,
    "match_reasons": [],
    "candidates": []
  },
  "status": "待用户确认"
}
```

表格项使用 `expected_type: table`，并通过 `result_schema` 记录列级多层表头，通过 `binding.target_cell_ids` 记录所有物理回填位置。

## 问题库匹配接口预留

查询模块或问题库匹配模块只需回写：

```json
{
  "status": "matched",
  "matched_question_id": "land_change_001",
  "confidence": 0.93,
  "match_reasons": ["指标一致", "统计范围一致", "单位一致"],
  "candidates": []
}
```

低置信度或多候选结果必须交给用户确认，不自动选择。

## 查询结果回传约定

查询模块建议按原子项 ID 返回：

```json
{
  "field_id": "field-001",
  "value": 12.35,
  "unit": "万亩",
  "query_time": "2026-09-18T15:00:00+08:00",
  "status": "success"
}
```

地区名称和对应面积通过 `related_to` 建立关系。回填模块不根据自然语言猜测位置，只按照 `field_id` 和 `binding` 写回模板副本。

## 验收原则

1. 每个占位值均有唯一 `field_id`。
2. 每个 `field_id` 均可追溯到原文和章节。
3. 每个查询结果均有唯一回填定位。
4. 历史示例值不得直接成为查询条件。
5. 无法稳定判断的字段保留“待用户确认”，不得静默推断。
