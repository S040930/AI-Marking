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

const MCP_ZH_TEMPLATE = `请继续使用 AI-Marking 批改作业。

题目：{questionName}（question_id: {questionId}）
该作业的报告 PDF 与代码文件已上传到 AI-Marking。

请先加载 ai-marking-grader skill 获取完整批改流程，
打开该作业的评分包后按流程批改。最终成绩由教师在网页确认。`;

const MCP_EN_TEMPLATE = `Continue grading the assignment with AI-Marking.

Question: {questionName} (question_id: {questionId})
The report PDF and code files have been uploaded to AI-Marking.

First load the ai-marking-grader skill for the full grading workflow,
then open the assignment's grading package and grade following it. The final grade is confirmed by the teacher on the web page.`;

const TEMPLATES: Record<Locale, string> = {
  'zh-CN': ZH_TEMPLATE,
  'en-US': EN_TEMPLATE,
};

const MCP_TEMPLATES: Record<Locale, string> = {
  'zh-CN': MCP_ZH_TEMPLATE,
  'en-US': MCP_EN_TEMPLATE,
};

export function buildGradingPrompt(input: GradingPromptInput, locale: Locale): string {
  return TEMPLATES[locale]
    .replaceAll('{questionName}', input.questionName)
    .replaceAll('{questionId}', String(input.questionId));
}

/** MCP 恢复提示词:作业已在 AI-Marking 中,让外部助手打开评分包继续批改。 */
export function buildMcpResumePrompt(input: GradingPromptInput, locale: Locale): string {
  return MCP_TEMPLATES[locale]
    .replaceAll('{questionName}', input.questionName)
    .replaceAll('{questionId}', String(input.questionId));
}

export interface AcpChatGradingPromptInput {
  questionName: string;
  submissionId: number;
}

/**
 * 内置 AI 助手的批改指令：教师点击「开始批改」后填入助手输入框，由教师确认
 * 配置后手动发送。语义与后端 ``orchestrator.build_grading_prompt`` 对齐——
 * ACP 模式提示词只做入口：题目与 submission_id 已给出，完整批改流程由助手读取
 * 工作区根目录的 ``skill.md``（ai-marking-grader skill 全文）获得，提示词不再
 * 内嵌流程细节，避免与 SKILL.md 双份漂移。
 */
const ACP_CHAT_TEMPLATES: Record<Locale, string> = {
  'zh-CN': `请批改 AI-Marking 作业《{questionName}》（submission_id={submissionId}）。

这是已存在且已完成 OCR 的作业：先读取工作区根目录的 skill.md 获取完整批改流程（内含模式判定与评分步骤），然后严格按其评分。流程与证据契约以该文件为唯一权威；请勿调用 prepare_ai_marking_submission 或 submit_prepared_ai_marking_submission。最终成绩由教师在网页确认，不要自行定稿。`,
  'en-US': `Please grade the AI-Marking assignment "{questionName}" (submission_id={submissionId}).

This is an already-existing assignment with OCR complete. First read skill.md in the workspace root for the full grading flow (it covers mode identification and grading steps), then grade strictly following it. That file is the single authority for the flow and evidence contract; do NOT call prepare_ai_marking_submission or submit_prepared_ai_marking_submission. The teacher confirms the final grade on the web page — do not finalize it yourself.`,
};

export function buildAcpChatGradingPrompt(
  input: AcpChatGradingPromptInput,
  locale: Locale,
): string {
  return ACP_CHAT_TEMPLATES[locale]
    .replaceAll('{questionName}', input.questionName)
    .replaceAll('{submissionId}', String(input.submissionId));
}
