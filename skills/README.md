# skills 目录

GeoAgent 的技能按需加载机制从这里读取技能。每个技能是一个包含 `SKILL.md` 的子目录：

```text
skills/
└── land-report/
    └── SKILL.md
```

## 如何新增一个技能

1. 在 `skills/` 下新建目录 `{skill_name}/`；
2. 在目录内创建 `SKILL.md`，推荐开头用 frontmatter 声明名称和描述：

```markdown
---
name: land-report
description: Generate land flow change briefing reports
---

# 土地流向变化快报

（完整指令……）
```

3. 重启后端（技能目录在启动时扫描）；模型可通过 `list_skills` 查看目录，
   需要完整指令时调用 `load_skill(name)` 加载。

## 说明

- 不写 frontmatter 时，技能名取目录名，描述取正文第一行；
- 技能名称只用于加载器注册表查询，不会作为文件路径使用；
- 部署时可用环境变量 `GEOAGENT_SKILLS_DIR` 把技能目录指到仓库外的路径；
- 技能随仓库版本管理，`SKILL.md` 的正文是给 LLM 看的完整指令，
  语言按 AGENTS.md 约定优先使用英文。
