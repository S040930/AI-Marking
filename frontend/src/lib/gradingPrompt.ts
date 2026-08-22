import type { Locale } from '@/i18n';

export interface GradingPromptInput {
  questionName: string;
  questionId: string;
}

const ZH_TEMPLATE = `使用 ai-marking-grader skill 批改学生作业。

题目：{questionName}（question_id: {questionId}）
学生作业：我在本次对话中上传的 zip 压缩包（一份报告 PDF 与若干代码文件）。

请先加载 ai-marking-grader skill 获取完整批改流程，
然后按流程上传作业 zip 并开始批改。最终成绩由教师在网页确认。`;

const EN_TEMPLATE = `Grade one student's assignment with the ai-marking-grader skill.

Question: {questionName} (question_id: {questionId})
Student assignment: the zip archive I uploaded in this conversation (one report PDF plus code files).

First load the ai-marking-grader skill for the full grading workflow,
then upload the assignment zip and start grading following it. The final grade is confirmed by the teacher on the web page.`;

const TEMPLATES: Record<Locale, string> = {
  'zh-CN': ZH_TEMPLATE,
  'en-US': EN_TEMPLATE,
};

export function buildGradingPrompt(input: GradingPromptInput, locale: Locale): string {
  return TEMPLATES[locale]
    .replaceAll('{questionName}', input.questionName)
    .replaceAll('{questionId}', String(input.questionId));
}
