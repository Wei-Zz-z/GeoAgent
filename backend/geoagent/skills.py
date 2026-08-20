"""技能加载器：扫描 skills 目录，按需提供 SKILL.md 内容。

设计参考 learn-claude-code s07：
- 启动时扫描 `{skills_dir}/*/SKILL.md`，解析可选 YAML frontmatter（name / description），
  建立名称注册表；名称只用于查表，不参与文件路径拼接（防路径穿越）。
- 技能目录（名称 + 描述）注入 system prompt；完整内容由 load_skill 按需返回。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class SkillNotFoundError(KeyError):
    """请求的技能不存在。"""


class SkillLoader:
    """技能目录扫描器与加载器。"""

    def __init__(self, skills_dir: str | Path) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills: dict[str, dict[str, str]] = {}

    def scan(self) -> None:
        """扫描 skills 目录，重建技能注册表。"""
        self.skills.clear()
        root = self.skills_dir.resolve()
        if not root.is_dir():
            return
        for manifest in sorted(root.glob("*/SKILL.md")):
            if not manifest.is_file():
                continue
            resolved = manifest.resolve()
            if not resolved.is_relative_to(root):
                continue
            try:
                content = resolved.read_text(encoding="utf-8")
            except OSError:
                continue
            metadata, body = self._parse_frontmatter(content)
            name = str(metadata.get("name", "")).strip() or manifest.parent.name
            description = str(metadata.get("description", "")).strip()
            if not description:
                first_line = next((line for line in body.splitlines() if line.strip()), "")
                description = " ".join(first_line.lstrip("#").strip().split())
            self.skills[name] = {
                "name": name,
                "description": description,
                "content": content,
            }

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
        """解析可选的 `---` frontmatter（仅支持简单的 key: value，不引入 YAML 依赖）。"""
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, content
        metadata: dict[str, str] = {}
        body_lines: list[str] = []
        in_frontmatter = True
        for line in lines[1:]:
            if in_frontmatter and line.strip() == "---":
                in_frontmatter = False
                continue
            if in_frontmatter:
                if ":" in line:
                    key, _, value = line.partition(":")
                    metadata[key.strip()] = value.strip()
            else:
                body_lines.append(line)
        return metadata, "\n".join(body_lines)

    def catalog(self) -> dict[str, dict[str, str]]:
        """技能目录（名称 + 描述），不含完整内容。"""
        return {
            name: {"name": info["name"], "description": info["description"]}
            for name, info in sorted(self.skills.items())
        }

    def catalog_prompt(self) -> str:
        """生成注入 system prompt 的技能目录文本（英文）。"""
        if not self.skills:
            return ""
        lines = ["Skills available:"]
        lines += [f"- {name}: {info['description']}" for name, info in self.catalog().items()]
        lines.append(
            "Use list_skills to see this catalog, and load_skill to read the full "
            "instructions when a skill applies."
        )
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """按名称返回完整 SKILL.md 内容；未知技能抛出 SkillNotFoundError。"""
        skill = self.skills.get(name)
        if skill is None:
            available = ", ".join(sorted(self.skills)) or "none"
            raise SkillNotFoundError(f"Unknown skill '{name}'. Available: {available}")
        return skill["content"]

    def to_dict(self) -> dict[str, Any]:
        return {"skills_dir": str(self.skills_dir), "skills": self.catalog()}
