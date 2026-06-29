# app/core/prompt.py
"""LLM 批改 prompt 模板。

默认 rubric 硬编码在本模块中;若调用方通过页面配置了自定义 rubric,
``build_user_prompt`` 会优先使用自定义值,为空时回退到默认。
"""

SYSTEM_PROMPT = """你是一位严谨、专业的大学课程作业批改助手。请根据提供的评分标准(rubric)对学生作业进行批改。
你必须严格按照要求的 JSON 格式输出,不要输出任何 JSON 之外的内容。"""

RUBRIC = """评分维度(rubric):
1. 内容理解(Content Understanding, 30分):是否准确理解题目要求与核心概念
2. 论证分析(Analysis & Argument, 30分):论证是否清晰、逻辑是否严密、是否有批判性思考
3. 结构组织(Structure & Organization, 20分):文章结构是否合理、段落是否清晰、过渡是否自然
4. 语言表达(Language & Expression, 10分):语言是否准确流畅、术语使用是否恰当
5. 规范性(Formatting & Citation, 10分):格式是否规范、引用是否符合学术规范
总分:100分"""

OUTPUT_FORMAT = """输出格式(严格 JSON,不要 markdown 代码块,不要额外说明):
{
  "score": <总分, 数字>,
  "feedback": "<总体反馈, 200字以内>",
  "details": [
    {"criterion": "<维度名>", "score": <该维度得分>, "comment": "<该维度具体评语>"},
    ...
  ]
}
注意:details 数组必须包含上述 5 个维度,score 之和应等于总 score。"""

USER_PROMPT_TEMPLATE = """请批改以下学生作业。

{rubric}

{output_format}

以下为作业原文(由 OCR 识别,可能含少量识别错误,请尽量理解):

---
{ocr_text}
---

请进行批改并按要求输出 JSON。"""


def build_user_prompt(
    ocr_text: str,
    rubric: str | None = None,
    user_prompt_template: str | None = None,
) -> str:
    """构造用户 prompt。

    Args:
        ocr_text: OCR 解析后的作业文本
        rubric: 自定义评分标准;为空/None 时使用内置默认 RUBRIC
        user_prompt_template: 自定义用户 prompt 模板;为空/None 时使用内置
            默认 USER_PROMPT_TEMPLATE。模板可包含 {rubric}、{output_format}、
            {ocr_text} 三个占位符,均会被自动填充;未使用的占位符会被忽略。
    """
    template = user_prompt_template if user_prompt_template else USER_PROMPT_TEMPLATE

    # 如果是自定义模板,先对非占位符花括号做转义,防止 KeyError
    if user_prompt_template:
        escaped = _escape_braces(template)
    else:
        escaped = template

    return escaped.format(
        rubric=rubric if rubric else RUBRIC,
        output_format=OUTPUT_FORMAT,
        ocr_text=ocr_text,
   )


def _escape_braces(template: str) -> str:
    """转义模板中除已知占位符之外的花括号。

    将 {{ 和 }} 替换为 {{{{ 和 }}}},然后将已知占位符恢复为单层花括号,
    这样 .format() 不会把用户输入的 JSON 样例等误认为占位符。
    """
    known = {"rubric", "output_format", "ocr_text"}
    # 先将已知占位符替换为临时标记
    placeholders = {f"{{{k}}}": f"\x00{k}\x00" for k in known}
    for raw, marker in placeholders.items():
        template = template.replace(raw, marker)
    # 转义所有剩余花括号: { -> {{, } -> }}
    template = template.replace("{", "{{").replace("}", "}}")
    # 恢复已知占位符
    for raw, marker in placeholders.items():
        template = template.replace(marker, raw)
    return template
