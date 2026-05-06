"""
steelg8 · docx 技能包
=====================

所有操作 Word 文档（.docx）的能力按"动作语义"分四个模块：

  fill   — 填模板：把 {{占位符}} 替换成真实数据
  grow   — 长新内容：加章节、加段落、加表格行、build_outline
  edit   — 改现有内容：替换 / 改标题 / 删段落 / 合规审查 / 插整表
  diff   — 比两版差异

内部辅助模块（不直接暴露给 LLM）：

  xml_io — docx 作为 zip+xml 的底层解包/重打包/校验工具

重构约定（2026-04-22）：
- 从 `Python/skills/docx_fill.py` 迁移到 `Python/skills/docx/fill.py`
- 外部 import 路径从 `from skills import docx_fill` 改为
  `from skills.docx import fill` 或 `from skills.docx import fill as docx_fill`（兼容旧命名）
"""

from skills.docx import diff, edit, fill, grow

# 向后兼容：旧代码里 `from skills import docx_fill as xxx` 的写法会坏，但
# `from skills.docx import fill as docx_fill` 可继续跑。这里导出别名。
docx_fill = fill
docx_grow = grow
docx_edit = edit
docx_diff = diff

__all__ = ["fill", "grow", "edit", "diff",
           "docx_fill", "docx_grow", "docx_edit", "docx_diff"]
