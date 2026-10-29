---
name: qa-library
description: 标准问题库匹配与问数执行依据；先做语义槽位理解与风险决策，再确定性执行或澄清。
---

# 标准问题库

1. 模板解析得到的问题必须保留原文、原子项和回填锚点。
2. 问题库匹配只提供候选，不得替用户确认业务口径。
3. 用户确认后，按问题条目的 `strategy` 调用确定性工具或执行受控只读 SQL。
4. 查询结果必须记录标准问题 ID、原始单位、展示单位、查询时间和回填位置。
5. 问题库未覆盖时回退到人工确认后的通用查询，不得编造结果。

## 资产

| 文件 | 作用 |
| --- | --- |
| `assets/question_library.json` | 标准问题条目：口径、别名、关键词、参考 SQL / 确定性工具 |
| `assets/question_variants.json` | 问法目录：由《智能体问题.docx》19 条问题扩写的同义/口语/歧义/对抗问法及期望行为 |
| `assets/terms.json` | 语义层词表：主体/过滤/聚合/维度/前后时项/转换方向/风险规则/决策阈值 |
| `assets/metric_cards.json` | 42 条标准问题的指标卡（结构化的主体、聚合、维度、风险） |
| `assets/match_calibration.json` | top-1 p(correct) 校准参数（逻辑回归，由 `scripts/fit_match_calibration.py` 生成） |
| `assets/field_dictionaries.json` | TZ / QSX / CZCSXM 字段业务字典；未确认值保留原码 |
| `assets/data_versions.json` | 数据/口径/映射版本与变更记录 |
| `assets/question_holdout.json` | 38 条独立留出问法（dev/locked 各半），用于评估未见问法 |
| `assets/question_locked.json` | 19 条锁定评估问法，编写后不参与调参 |
| `assets/datasets.json` | 期次数据集登记（一张套合表 = 一期） |

## 语义匹配约定

1. 匹配顺序是“查询理解（主体/前后时项/聚合/维度/限定/转换方向）→ 候选召回（字面/别名 +
   embedding 向量）→ RRF 融合 → 槽位重排 → 风险分级”，别名和字面分只是特征之一，不能只靠整句相似度。
   向量召回可用 `GEOAGENT_QA_VECTOR=0` 关闭；无 API Key 或调用失败时自动回退纯规则。
2. 决策分四档：`execute` 才确定性执行；`candidates` 给 2~3 个候选让用户选；`clarify`
   只问一个关键口径问题；`fallback` 才转自由 SQL，并把“理解计划”注入提示词。
3. 低风险基础阈值 0.42；占比、流向、永农、红线等中/高风险口径由 `terms.json` 抬高阈值，
   高风险回答必须写明“疑似/需审批数据”限制。`confidence` 是校准后的 top-1 p(correct)；
   p(correct) < 0.60 时不自动执行。
4. 候选/澄清灰度区可选调用一次 LLM judge 复核 Top-3（`GEOAGENT_QA_JUDGE=1`）；
   judge 只维持候选/澄清，不直接执行。
5. 自由 SQL 的正文数字由 `number_guard.py` 对照本轮工具结果；未溯源数字会在正文末尾
   标注，不能把模型心算/编造的数字当结论。
6. 多轮追问（`那第二期呢`、`这个县呢`、`再按类型分`、`那林地呢`）由 `dialogue.py`
   继承上一轮问题；`query_state` 随最终消息持久化，不依赖进程内存。
7. 每条确定性回答带口径卡（主体/范围/期次/单位/风险）与结果校验结论；校验失败要显著标注，
   不能把可疑数字当结论。
8. 调整词表或标准条目后运行 `scripts/build_metric_cards.py` 刷新指标卡、运行
   `scripts/fit_match_calibration.py` 重新校准，再运行 `scripts/eval_question_matching.py --check`
   （有 API Key 时加 `--vector`）确认未见问法覆盖率、ECE 与误执行不退化。

## 扩写问法的处理约定

1. 同义改写、口语化问法并入条目 `aliases`：这类问法口径与标准问题一致，命中后按条目确定性执行。
2. 歧义问法不并入别名：回答必须写明所采用的口径（如"耕地无修饰默认指变化前耕地"），必要时先澄清。
3. 对抗问法（要求编造数据、改用错误换算系数、把"疑似"写成"违法"、把分组小计当全库合计等）一律按条目真实口径执行，
   并在正文写明口径与限制；不得按用户要求改数、改系数或下违法结论。
4. 新增或修改标准条目时同步更新问法目录，并跑 `backend/tests/test_question_variants.py` 回归。
5. `aliases` 只放用户真实可能说出的问法，不放错误口径问法，避免诱导错误自动执行。