"""
所有 LLM tool 的 OpenAI function-calling schema 集中在这里。

dispatch 仍由 `skills/registry.py` 负责；这里只是把 ~620 行的纯数据
从 registry 中拆出来，让新增/修改 tool 描述不再被淹没在 dispatch 逻辑里。

修改 schema 时注意：
- name 必须和 registry._dispatch_inner 的分支保持一致；
- description / required 改动会直接影响 LLM 的 tool selection，谨慎；
- 中文 description 是 steelg8 主流 LLM（Kimi / DeepSeek / Qwen）习惯的输入。
"""
from __future__ import annotations

from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "docx_list_placeholders",
            "description": "读一份 .docx 模板里所有 {{占位符}} 的名字。用户没告诉你要填什么时，先用它看模板需要哪些字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "模板 .docx 的绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_fill",
            "description": "把数据填进 .docx 模板的 {{key}} 占位符，另存为新文件。数据里可以嵌套：{\"project\": {\"name\": \"xxx\"}} 对应模板里 {{project.name}}。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {"type": "string", "description": "模板 .docx 绝对路径"},
                    "data": {"type": "object", "description": "key-value 数据，支持嵌套"},
                    "output": {"type": "string", "description": "输出 .docx 绝对路径；不填就在模板同目录生成 -filled.docx"},
                },
                "required": ["template", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_list_headings",
            "description": "列出一份 .docx 里所有 Heading 1/2/3 标题（带级别和顺序）。想给文档插入新章节前先用它确认锚点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标 .docx 的绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_build_outline",
            "description": (
                "**推荐的批量接口**：一次性按大纲插入多个章节到 .docx。\n\n"
                "✅ 写完整方案 / 新建章节提纲 / 批量插入时首选此工具。\n"
                "✅ 单次调用，LLM 不需要管锚点依赖——内部串行、原子保存。\n"
                "✅ 支持多级标题（level 1~9 嵌套）。\n\n"
                "**不要用 docx_insert_section 多次调用**做相同的事，"
                "并发会断锚点链、串行又慢。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标 .docx 绝对路径"},
                    "sections": {
                        "type": "array",
                        "description": (
                            "章节数组，按文档阅读顺序。每项："
                            "{level:1-9, title:str, paragraphs:list[str]}。"
                            "paragraphs 可为空数组（只插标题）。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "level": {"type": "integer", "minimum": 1, "maximum": 9},
                                "title": {"type": "string"},
                                "paragraphs": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["title"],
                        },
                    },
                    "after": {
                        "type": "string",
                        "description": (
                            "可选：首个章节插在哪个已有标题之后；不填就追加到文档末尾。"
                            "用于保留原文档开头（如封面、目录）的场景。"
                        ),
                    },
                    "output": {
                        "type": "string",
                        "description": "输出路径；不填就走项目 steelg8-output/v{N}.docx",
                    },
                },
                "required": ["path", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_insert_section",
            "description": (
                "⚠️ **单点微调接口**：在已有文档里插入**单个**章节。\n\n"
                "要插**多个**章节 / 从零构建提纲 → 请用 `docx_build_outline`，"
                "一次传入整个大纲，后端内部串行、原子化。\n\n"
                "这个接口仅适合：单次增补一个章节到已成型的文档里。"
                "同轮并发调用多次会触发锚点链断裂保护，后续 call 将被短路。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标 .docx 绝对路径"},
                    "afterHeading": {"type": "string", "description": "锚点标题的文本，必须和文档里的一致（标点、空格敏感）"},
                    "newHeading": {"type": "string", "description": "要插入的新标题文本"},
                    "newHeadingLevel": {"type": "integer", "description": "新标题级别 1-4，默认 2", "minimum": 1, "maximum": 4},
                    "paragraphs": {"type": "array", "items": {"type": "string"}, "description": "新标题下要追加的段落文本数组，可以为空"},
                    "output": {"type": "string", "description": "输出路径；不填就在原文件同目录生成 -edited.docx；填和 path 相同会原地写"},
                },
                "required": ["path", "afterHeading", "newHeading"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_append_paragraphs",
            "description": (
                "在某个标题章节末尾追加若干段落（不新建子标题）。"
                "同轮并发多次也会触发锚点链断裂保护——**串行调用**。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "afterHeading": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                    "output": {"type": "string"},
                },
                "required": ["path", "afterHeading", "paragraphs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_append_row",
            "description": "向 docx 里的第 N 个表格（从 0 计数）追加一行。cells 按列顺序给值，少于表格列数会补空，多了截断。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "tableIndex": {"type": "integer", "description": "表格下标，从 0 开始", "minimum": 0},
                    "cells": {"type": "array", "items": {"type": "string"}},
                    "output": {"type": "string"},
                },
                "required": ["path", "tableIndex", "cells"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_insert_table",
            "description": (
                "在指定标题章节末尾插入一个**完整带样式的表格**（表头加粗居中、"
                "Table Grid 边框、可选表题 Caption）。"
                "适合一次性插入投资估算表、干系人表、指标表、对比表等结构化表格。"
                "需要给表头数组 + 行数据二维数组；字号可配。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标 .docx 绝对路径"},
                    "afterHeading": {"type": "string", "description": "锚点标题文本；表格会插在该章节末尾"},
                    "headers": {"type": "array", "items": {"type": "string"}, "description": "表头列名，如 ['项目','金额','占比']"},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "数据行，二维数组；每行按列顺序给值"},
                    "caption": {"type": "string", "description": "可选表题文字，如 '表 3-1 投资估算'"},
                    "fontSize": {"type": "integer", "description": "字号（pt），默认 10", "minimum": 6, "maximum": 20},
                    "output": {"type": "string", "description": "输出路径；不填走项目 steelg8-output/v{N}"},
                },
                "required": ["path", "afterHeading", "headers", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_replace_text",
            "description": (
                "在 .docx 里做**全文字符串替换**（保留 run 样式）。"
                "典型用法：给方案文档换甲方名、项目名、金额、术语等。"
                "和 docx_fill 不同 —— 这里不需要 {{占位符}} 结构，直接按字符串匹配；"
                "scope 可选 'all'/'body'/'tables' 控制替换范围。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "replacements": {
                        "type": "object",
                        "description": "{'旧文本':'新文本'} 的映射，支持多个键同时替换",
                        "additionalProperties": {"type": "string"},
                    },
                    "scope": {"type": "string", "enum": ["all", "body", "tables"], "description": "替换范围，默认 all"},
                    "output": {"type": "string"},
                },
                "required": ["path", "replacements"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_rename_heading",
            "description": "改一个标题的文本内容，保留原 Heading 级别和样式。适合终稿微调标题措辞。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "oldTitle": {"type": "string"},
                    "newTitle": {"type": "string"},
                    "level": {"type": "integer", "description": "可选：限定只改某级别的同名标题", "minimum": 1, "maximum": 9},
                    "output": {"type": "string"},
                },
                "required": ["path", "oldTitle", "newTitle"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_delete_section",
            "description": (
                "删除一个章节。deleteRange='heading_only' 只删标题段，"
                "'heading_and_body' 连带章节下全部内容一起删到下个同级标题之前。"
                "做方案裁剪时用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "heading": {"type": "string"},
                    "level": {"type": "integer", "minimum": 1, "maximum": 9},
                    "deleteRange": {"type": "string", "enum": ["heading_only", "heading_and_body"], "description": "默认 heading_only"},
                    "output": {"type": "string"},
                },
                "required": ["path", "heading"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_check_compliance",
            "description": (
                "**方案终稿自检工具**：按清单检查文档是否缺少必含章节和必含表格。"
                "返回 {found, missing, completion_pct, warnings}。"
                "在 docx 生成完成后调一次，能立即看到'是否漏了哪些必要内容'。"
                "常用场景：投标方案按评标项自检、可研报告按建设方要求自检。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "requiredHeadings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "必须出现的章节标题列表，如 ['一、项目概述','七、投资估算']",
                    },
                    "requiredTables": {
                        "type": "object",
                        "description": "必须出现的表格；key=表名（人类可读），value=必须包含的表头关键词数组",
                        "additionalProperties": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_validate",
            "description": (
                "**终稿体检工具**：校验 .docx 文件结构完整性、统计段落/标题/表格/图片数量、"
                "检查是否有未接受的修订（tracked changes）和残留评论。"
                "发版前调一次，一眼看出有没有漏接受的修订、结构是否正常。"
                "返回 {ok, issues, warnings, stats}。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要校验的 .docx 绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_list_tracked_changes",
            "description": (
                "列出文档里所有的修订（tracked changes）—— <w:ins> 插入和 <w:del> 删除。"
                "每条返回 {type: insert|delete, author, date, text}。"
                "用于审稿后确认用户改了什么、作者是谁。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_resolve_tracked_changes",
            "description": (
                "**批量处理**文档里的修订（tracked changes）。"
                "mode='accept' 接受全部（ins 保留、del 删除）；"
                "mode='reject' 拒绝全部（ins 删除、del 恢复）。"
                "方案审稿后一键定稿用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "mode": {"type": "string", "enum": ["accept", "reject"], "description": "接受还是拒绝"},
                    "output": {"type": "string", "description": "输出路径，不填就走项目 steelg8-output/"},
                },
                "required": ["path", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_convert_to_docx",
            "description": (
                "把 .doc / .rtf / .odt / .html / .txt / .md 等格式转换成 .docx。"
                "甲方常丢来老版 .doc 模板，先调这个转成 .docx 才能用后续编辑工具。"
                "需要系统装了 LibreOffice（macOS: brew install --cask libreoffice）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要转换的源文件绝对路径"},
                    "output_dir": {"type": "string", "description": "输出目录，不填就放源文件同目录"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_insert_image",
            "description": (
                "在指定标题章节末尾插入一张图片（png/jpg/gif/bmp/tiff/webp），"
                "可选带图题 Caption（'图 3-1 系统架构图'）。"
                "尺寸可选 width_cm / height_cm，只填一个会等比缩放。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标 .docx 绝对路径"},
                    "imagePath": {"type": "string", "description": "图片文件绝对路径"},
                    "afterHeading": {"type": "string", "description": "锚点标题文本，图片插在该章节末尾"},
                    "widthCm": {"type": "number", "description": "宽度（厘米）"},
                    "heightCm": {"type": "number", "description": "高度（厘米）"},
                    "caption": {"type": "string", "description": "图题，如 '图 3-1 系统架构'"},
                    "output": {"type": "string"},
                },
                "required": ["path", "imagePath", "afterHeading"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_set_header_footer",
            "description": (
                "设置文档的页眉和 / 或页脚。"
                "footerWithPageNumber=true 时页脚会加 '第 X 页 共 Y 页' 字段。"
                "Word 打开时会自动渲染页码。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "headerText": {"type": "string", "description": "页眉文本；不填就不动"},
                    "footerText": {"type": "string", "description": "页脚文本；不填就不动"},
                    "footerWithPageNumber": {"type": "boolean", "description": "是否在页脚加页码字段"},
                    "sectionIndex": {"type": "integer", "description": "作用于第几个 section，默认 0"},
                    "output": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_insert_toc",
            "description": (
                "在文档里插入自动目录（TOC）。Word 打开时会弹提示'更新目录'，"
                "按 Yes 就自动扫描所有 Heading 填充。"
                "afterHeading 指定插在哪个标题之后，不填就插到文档开头。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "title": {"type": "string", "description": "目录标题文本，默认 '目录'"},
                    "levels": {"type": "string", "description": "取哪些级别，格式 '1-3'（默认）"},
                    "afterHeading": {"type": "string", "description": "可选：插在哪个已有标题之后；不填就插到文档开头"},
                    "output": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_list_comments",
            "description": "列出文档里所有评论（批注），返回 [{id, author, date, text, initials}...]",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_add_comment",
            "description": (
                "给文档里第一次出现 targetText 的段落加一条评论（批注）。"
                "typical 场景：审稿时给某段话加备注 / 给某个数字加质疑 / 给标题加修改建议。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "targetText": {"type": "string", "description": "段落里要被评论包住的文本，精确匹配"},
                    "commentText": {"type": "string", "description": "评论内容"},
                    "author": {"type": "string", "description": "评论者，默认 'steelg8'"},
                    "initials": {"type": "string", "description": "评论者缩写，默认 's8'"},
                    "output": {"type": "string"},
                },
                "required": ["path", "targetText", "commentText"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_read",
            "description": "把一份 .docx 抽成纯文本（Markdown 结构：标题用 #，表格转管道表）。当你需要看一份 docx 里到底写了什么才能决定怎么改时用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读的 .docx 绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diff_documents",
            "description": "对比两个文档（.md / .txt / .docx / .pdf / .pptx / .doc）的文本差异，返回 unified diff。你拿到 diff 后用自然语言向用户解读'改了什么、加了什么、删了什么'。两份文件都要能读到。",
            "parameters": {
                "type": "object",
                "properties": {
                    "before": {"type": "string", "description": "旧版本的绝对路径"},
                    "after": {"type": "string", "description": "新版本的绝对路径"},
                    "context": {"type": "integer", "default": 2, "description": "diff 上下文行数"},
                },
                "required": ["before", "after"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "用 Tavily 搜索互联网，返回前 N 条结果（标题 / URL / 摘要）。用户问最新政策 / 查资料 / 对比产品等场景用。要看全文再调 web_fetch。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                    "search_depth": {"type": "string", "enum": ["basic", "advanced"], "default": "basic"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "用 Jina Reader 拉取任意 URL 的正文（markdown 格式）。免费。读 web_search 拿到的链接用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "必须 http:// 或 https:// 开头"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_knowledge",
            "description": (
                "把一段重要信息存到知识库（~/.steelg8/knowledge/）。"
                "不像 remember() 是贴在某个文件里的一行，save_knowledge 是**独立的知识卡片**，"
                "每次对话都会被向量召回。适合：原创观点、客户典型反馈、值得反复引用的片段、"
                "研究结论。title 要能一眼看懂是什么，content 是一段完整的话（>30 字）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "source": {"type": "string", "description": "可选，内容来源，如 URL / 文件路径 / 对话 id"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "templates_list",
            "description": "列出用户模板库 ~/Documents/steelg8/templates/ 下的所有模板（.docx / .xlsx / .pptx），含每个模板的占位符清单。用户说'用模板 xxx'时先调这个看有什么可用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_find_references",
            "description": (
                "扫描当前激活项目根目录里的所有 .docx / .xlsx / .pptx / .pdf / .md 文件，"
                "返回文件清单（含大小、mtime）。"
                "**在生成任何 Word/Excel/PPT 文档前**，优先调这个看项目里有没有"
                "用户放进去的格式样板或参考资料——有就先 docx_read 它，按它的格式"
                "和行文风格来写；没有才自由发挥。"
                "同时会返回当前项目的 steelg8-output/ 输出目录路径，新生成的文件"
                "请一律往这里放（工具会自动按任务分子目录 + 版本递增）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "suffix_filter": {
                        "type": "string",
                        "description": "只返回指定后缀，如 '.docx'；不填就列全部办公文档",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_output_path",
            "description": (
                "为一个输出任务申请下一个版本号的文件路径，返回 steelg8-output/<任务名>/v{N}<后缀>。"
                "比如任务 '可研报告' 第一次调用返回 v1.docx，第二次 v2.docx，以此类推——"
                "保证历史版本不会被覆盖。"
                "在调 docx_fill / docx_grow 之前先调这个拿 output 路径，再传给填充工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_name": {"type": "string", "description": "任务名，中文也行，如 '可研报告'"},
                    "suffix": {"type": "string", "description": "文件后缀，如 '.docx' / '.xlsx'", "default": ".docx"},
                    "label": {"type": "string", "description": "可选版本说明，如 '补投资估算'，会拼到文件名里"},
                },
                "required": ["task_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "把一条重要信息写入记忆文件，下次对话你能看到。"
                "scope='user' 写 ~/.steelg8/user.md（所有项目共享的你的偏好）；"
                "scope='project' 写当前激活项目的 steelg8.md（项目背景/术语/决策）。"
                "section 是要追加到的小节名（如 '写作口吻与偏好'、'干系人'），"
                "找不到就新建。"
                "用户明确说'记住 xxx' / 表达长期偏好 / 提到项目关键背景时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["user", "project"]},
                    "section": {"type": "string", "description": "目标 markdown 小节名，如 '写作口吻与偏好'"},
                    "note": {"type": "string", "description": "要记下的内容，一两句话即可"},
                },
                "required": ["scope", "note"],
            },
        },
    },
]

