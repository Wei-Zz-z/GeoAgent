"""土地资源变化统计 SQL 问答 Agent（中文提示词）。

提示词只承载工具 schema 表达不了的内容：表结构与 join 坑、统计口径默认值、
SQL 纪律、确定性工具优先级与正文/卡片分工。每个工具“是什么、参数怎么填、
适用哪类问题”统一由工具注册时的 description 承担，此处不复述，避免两处说法漂移。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..core.agent import Agent
from ..dialogue import followup_hint, remember_query_state, resolve_followup_smart
from ..number_guard import analyze_claims, apply_corrections, evidence_digest
from ..prematch import build_period_prompt, resolve_turn_dataset, try_standard_answer
from ..tools.chart import get_chart_tools
from ..tools.knowledge import get_knowledge_tools
from ..tools.pg import get_sql_tools
from ..tools.qa_library import get_qa_tools
from ..tools.report import get_report_tools
from ..tools.registry import Tool
from ..tools.stat import get_stat_tools
from ..tools.warning import get_warning_tools

SQL_SYSTEM_PROMPT = (
    "你是 GeoAgent 的土地资源变化统计智能体，用只读 SQL 回答土地前后变化问题。"
    "所有数字必须来自工具返回的查询结果，禁止编造或心算；编码/名称/大类必须来自下方字典表；"
    "每次回答都说明统计口径与单位（面积统一用亩：1 亩 = 666.6667 平方米，即 1 平方米 = 0.0015 亩；库内 MJ 为平方米）。\n\n"
    "## 1. 可查询表（只读白名单）与关系\n"
    '- 变化图斑主表（别名 t，当期表名见系统提示末尾的"当前期次"块）：每行同时携带原土地类型'
    '（变化前，三调二级类："DLBM" 编码 / "DLMC" 名称）与图斑类型（变化后："TBLX" 影像编码，'
    '用 dict_tblx 查名称）；"MJ" = 面积（库内单位平方米，值域 50-200，对外一律换算为亩展示）；"XZQDM"/"XMC" = 县级行政区'
    '代码/名称；"QSX"/"HSX" = 前时相/后时相；"SFYN"/"SFHX" = 是否涉及永农/红线；'
    "其余字段用 describe_table 查看。\n"
    '- knowledge_base.dict_tblx：TBLX 编码→名称（"TBLX"=编码，"TBLX_CODE"=名称）。\n'
    "- knowledge_base.dict_land_classification_summary（合并地类字典）：原土地类型（三调）"
    '二级类→一级类→三大类。"DLBM"/"DLMC"=二级类编码/名称；'
    '"YJLBM"/"YJLMC"=一级类编码/名称；"CATEGORY_NAME"=三大类'
    "（农用地/建设用地/未利用地，扩展码可能为 NULL）。"
    '列一级类用 SELECT DISTINCT "YJLBM","YJLMC"。\n'
    "已实测的 join 规则：\n"
    "- 取 TBLX 名称必须处理前导零：ON t.\"TBLX\" = d.\"TBLX\" OR t.\"TBLX\" = '0' || d.\"TBLX\""
    "（数据 01-04 对应字典 1-4，否则约 2.4 万个图斑匹配不上）。\n"
    '- 原类型 join：ON t."DLBM" = d."DLBM"；编码 1208 后备耕地不在字典（489 个图斑），'
    '必须 LEFT JOIN，名称缺失时用 t."DLMC" 兜底，不可丢行或编造。\n\n'
    "## 2. 口径默认值（必须遵守，并说明用的是哪一侧）\n"
    "- 地类名称/编码、耕地、建设用地、农用地、未利用地 无修饰时 → 原土地类型（三调）；"
    "耕地（原）= \"DLBM\" LIKE '01%'。\n"
    "- 变化后/图斑类型为耕地 → \"TBLX\" = '01'；仅提“图斑”不改变口径，"
    "“每种类型”默认指图斑类型（变化后）。\n"
    "- 原三调建设用地（含扩展码）canonical 过滤：\"DLBM\" ~ "
    '\'^(05|06|07|08|09|100[1-5]|100[7-9]|1109|1201)\''
    "（等价 CATEGORY_NAME='建设用地' 并覆盖扩展码；CATEGORY_NAME 为 NULL 的扩展码用该正则"
    "判断，不要丢弃）。\n\n"
    "## 3. TBLX → 三大类（默认模糊映射模板；用户给出正式规则后以正式规则为准）\n"
    "- 农用地：01/1-04/4、ND、KT、SK、GQ\n"
    "- 建设用地（含疑似）：20、YH、DT、TD、DL1、DL2、DL3、TL、SJ、CK、YT、WL、TP、GF、"
    "LD、JC、ZQC、GEF、TH\n"
    "- 未利用地：SM、HL、LT、LY、WH、QT\n"
    "前后时项比较统一在三大类层面：前时项用三调三级真实映射，后时项用该模糊映射。\n\n"
    "## 4. SQL 纪律\n"
    "1. 只对白名单表执行只读 SELECT；允许 JOIN。\n"
    "2. 所有标识符按原文加双引号；主表名以数字开头必须保持引号（用系统提示末尾"
    '"当前期次"块给出的表名原文）。\n'
    "3. 相信上表结构：01-04 是前导零写法；除“列出地类清单”外，不要用 SELECT DISTINCT "
    "等探索查询去验证编码/映射。\n"
    "4. 名称/大类一律 JOIN 字典表或查询字典表获得；不要自创编码或名称。\n"
    "5. 优先聚合而非明细（结果上限 200 行）；Top-N 明细用 "
    'ORDER BY "MJ" DESC, "OBJECTID" LIMIT N。\n'
    "6. 分组统计的合计必须来自独立 COUNT/SUM 查询，禁止只把显示出来的分组相加，"
    "并核对分组之和等于合计；合计与关键分组写入正文（完整明细由表格卡片展示，见第 6 节）。\n"
    "7. 百分比只能引用查询结果里的列，禁止用显示出来的行自行心算。\n"
    "8. 查询失败时读错误、改 SQL、重试一次；不要陷入探索性查询循环。\n""9. 面积单位：库内 MJ 与所有 area/*_m2 列都是平方米，正文、表格卡片与图表一律按 ""1 平方米 = 0.0015 亩 换算为亩展示，保留两位小数；同一条回答不得混用平方米与亩。""（只列地类清单等不含面积的回答除外）\n\n"
    "10. 计算列与聚合列的别名一律用中文（如 AS 数量、AS 面积(亩)、AS 图斑类型名称），"
    "前端表格卡片直接展示列名，不要用 cnt / area_mu / tblx_name 这类英文别名。\n\n"
    "## 5. 确定性统计工具优先\n"
    "summarize_by_type / fragment_stats / farmland_flow_summary / "
    "construction_change_summary / top_conversions 已把 join、口径与合计固化在后端"
    "（各自适用场景见工具描述）。问题匹配这些模式时优先调用对应工具（通常一次调用即可），"
    "不要手写等价 SQL。\n"
    "预警类问题（“疑似违规/占用永久基本农田的图斑有哪些、分布在哪”“红线内有没有新增建设”"
    "“把图斑画到地图上”）用 warning_yongnong_polygons / warning_redline_polygons："
    "它们会返回图斑边界 GeoJSON，由前端红色叠加底图展示；当前期次没有几何数据时只给属性明细，"
    "并在回答里说明无法出图，不要拿别的期次几何顶替。"
    "**问题里限定了某个县/区（如“缙云县”）时必须传 county 参数**，否则统计到的是全库口径。"
    "带区县筛选的一般统计（如“某县耕地流出”）直接用 run_sql 加 \"XMC\" 条件。\n\n"
    "## 6. 正文与卡片分工\n"
    "表格/图表卡片负责完整展示明细，正文是卡片之上的总结，不是卡片的复述：\n"
    "1. 先直接回答用户的问题；关键数字（总量、净变化、最大项等）在正文出现一次即可，"
    "同时保留口径与单位（面积用亩）。\n"
    "2. 卡片已完整展示明细时，正文只提炼 1-3 个最值得注意的要点（最大流向/来源、集中区域、"
    "明显增减），不要逐行逐类复述面积与图斑数。\n"
    "3. 只有用户明确要求“列出”“详细说明”时，才在正文展开完整明细。\n"
    "4. generate_briefing 返回后，严格沿用工具给出的数字格式和单位：面积使用亩，"
    "所有数字不得添加千分位逗号；不得把工具结果重新格式化成带逗号的数字。"
    "**不要在正文里输出下载链接、文件路径或“### 下载链接”之类的段落**——"
    "Word/PDF 文件已经以卡片形式渲染，正文只给期次、关键数字和一句提示。\n"
    "构成/占比、类别对比、趋势变化类问题调用 make_chart 生成图表（选型与参数见工具描述），"
    "图表数据必须与回答口径、单位一致，且只取自查询结果。\n\n"
    "## 7. 标准问题与字段知识\n"
    "遇到常见统计问题但不确定查询口径时，先用 search_question_library 查标准问题及参考 SQL；"
    "不确定字段名、字典编码或表关系时，先用 search_schema_knowledge 检索已建立的知识库，"
    "未命中再用 list_tables、describe_table 或只读 SQL 核实。检索结果是线索，不能代替数据库验证。\n"
    "与地类变化统计无关的问题（如天气、股票、工资）应直接说明服务范围，不调用数据库工具。"
)


class SQLAgent(Agent):
    """土地变化统计 SQL 问答 Agent（路由目标: sql）。

    一轮对话的执行顺序是"先指标匹配、后自由 SQL 兜底"：

    1. 解析期次：前端显式指定 → 问题文本识别 → 默认期次（未登记期次直接提示，不猜）；
    2. 标准问题指标匹配：命中且置信度达标时按条目口径确定性执行，数字与口径不经模型；
    3. 未命中或执行失败：进入 LLM 工具循环自由查询，并把当期期次与候选口径作为提示。
    """

    BASE_SYSTEM_PROMPT = SQL_SYSTEM_PROMPT

    def __init__(
        self,
        model: Optional[str] = None,
        tools: Optional[list[Tool]] = None,
    ) -> None:
        super().__init__(
            name="sql_query",
            system_prompt=SQL_SYSTEM_PROMPT,
            tools=tools
            if tools is not None
            else get_sql_tools() + get_report_tools() + get_stat_tools()
            + get_chart_tools() + get_qa_tools() + get_knowledge_tools()
            + get_warning_tools(),
            model=model,
            max_turns=12,
        )

    async def exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        question = str(payload)
        ctx.current_question = question
        ctx.prematch_hint = ""
        ctx.followup_notice = ""
        # 0) 多轮追问补全：那第二期呢 / 这个县呢 / 再按类型分。
        effective_question, followup = await resolve_followup_smart(ctx, question)
        ctx.followup = {**followup, "last_user": question}
        ctx.effective_question = effective_question
        ctx.followup_notice = str(followup.get("notice") or "")
        # 1) 期次：本轮所有查询（标准问题与自由 SQL）都基于同一期数据。
        resolve_turn_dataset(ctx, effective_question)
        # 2) 标准问题指标匹配：命中即确定性执行，口径最稳。
        prematch = await try_standard_answer(
            ctx,
            question,
            match_text=effective_question,
            original_question=question,
        )
        if prematch is not None:
            return "default", prematch
        # 3) 未命中：把当期期次、追问补全与命中口径写进提示词，交给自由 SQL 工具循环兜底。
        self.system_prompt = self.BASE_SYSTEM_PROMPT + build_period_prompt(ctx)
        hint = followup_hint(ctx)
        if hint:
            ctx.prematch_hint = "\n\n".join(
                block for block in (ctx.prematch_hint, hint) if block
            )
            self.system_prompt = self.BASE_SYSTEM_PROMPT + build_period_prompt(ctx)
        ctx._turn_start_index = len(ctx.session.history())
        result = await super().exec(ctx, payload)
        remember_query_state(ctx, question, effective_question)
        return result

    async def finalize_answer(self, ctx: Any, final: Any) -> Any:
        """自由 SQL 回答出口：claim-level 校验 → 自动修正 → 重写/阻断。"""
        final.content = re.sub(r"(?m)^\s*#{1,6}\s*", "", final.content or "")
        start = int(getattr(ctx, "_turn_start_index", 0) or 0)
        messages = list(getattr(ctx, "turn_tool_messages", None) or [])
        if not messages:
            messages = ctx.session.history()[start:]
        question = str(getattr(ctx, "current_question", "") or "")
        claims = analyze_claims(final.content or "", messages, question=question)
        rewrite_attempted = False
        blocked = False

        if claims.get("corrections"):
            final.content = apply_corrections(final.content or "", claims["corrections"])
            claims = analyze_claims(final.content or "", messages, question=question)

        if not claims.get("ok"):
            rewritten = await self._rewrite_answer(ctx, final.content or "", claims, messages)
            rewrite_attempted = True
            if rewritten:
                final.content = re.sub(r"(?m)^\s*#{1,6}\s*", "", rewritten)
                claims = analyze_claims(final.content or "", messages, question=question)
                if claims.get("corrections"):
                    final.content = apply_corrections(final.content or "", claims["corrections"])
                    claims = analyze_claims(final.content or "", messages, question=question)

        if not claims.get("ok"):
            blocked = True
            final.content = (
                "本轮查询已完成，但回答中的部分数字无法与查询结果对应。"
                "为避免误导，正文不再给出未经核实的数字。\n\n"
                "请以上方查询结果表格为准；如需继续，请缩小统计范围，"
                "或明确期次、区县和统计口径。"
            )

        remember_query_state(
            ctx,
            question,
            str(getattr(ctx, "effective_question", "") or ""),
        )
        ctx.answer_metadata = {
            "number_guard": {
                **claims,
                "rewrite_attempted": rewrite_attempted,
                "blocked": blocked,
            },
            "query_state": dict(ctx.query_state),
        }
        return final

    async def _rewrite_answer(
        self,
        ctx: Any,
        content: str,
        claims: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> Optional[str]:
        """用本轮工具证据重写一次；失败返回 None，由调用方阻断。"""
        llm = getattr(ctx, "llm", None)
        if llm is None:
            return None
        unverified = "、".join(
            f"{item.get('raw')}{item.get('unit') or ''}"
            for item in (claims.get("unverified") or [])[:8]
        )
        evidence = evidence_digest(messages)
        prompt = (
            "请重写下面的回答，要求：\n"
            "1. 只能使用“工具证据”中出现的数字；不得新增、心算或改写数字；\n"
            "2. 无法确认的数字必须删除该数字或改成定性描述，不能保留原数字；\n"
            "3. 保留期次、范围、单位和统计口径说明；\n"
            "4. 输出纯文本，不要 Markdown 标题或代码块。\n\n"
            f"用户问题：{getattr(ctx, 'current_question', '') or ''}\n\n"
            f"当前回答：\n{content}\n\n"
            f"无法溯源的数字：{unverified}\n\n"
            f"工具证据：\n{evidence}"
        )
        try:
            message = await llm.chat(
                model=str(getattr(ctx, "model", "") or ""),
                messages=[
                    {"role": "system", "content": "你是严谨的地理统计回答校订器。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=800,
            )
        except Exception:
            return None
        text = str(getattr(message, "content", "") or "").strip()
        return text or None
