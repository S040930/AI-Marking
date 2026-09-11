import type { components } from './generated';

// 作业提交相关类型定义与纯状态判断函数。
// hooks 与请求逻辑在 ./submissions.ts 中,并通过 re-export 保持统一出口。

export type SubmissionStatus = components['schemas']['SubmissionStatus'];

export type AiSuggestionDetail = components['schemas']['ScoreDetail'];

export interface AiSuggestion {
  score: number;
  max_score: number;
  feedback: string;
  details: AiSuggestionDetail[];
  confidence: number;
  mcp_metadata?: components['schemas']['McpMetadataOut'];
}

export type AssessmentReview = components['schemas']['AssessmentReviewOut'];

export type SubmissionOut = components['schemas']['SubmissionOut'];

export interface SubmissionCodeFile {
  id: number;
  question_number: number;
  original_filename: string;
  file_kind: string;
  source_sha256: string;
  source_text: string | null;
  execution_status: 'pending' | 'running' | 'completed' | 'failed';
  execution_result: {
    stdout?: string;
    stderr?: string;
    exception?: string | null;
    failure_kind?: string | null;
    notebook_outputs?: Array<{ cell: number; text?: string; error?: string }>;
  } | null;
  artifacts: Array<{
    filename: string;
    artifact_id: string;
    kind: string;
    size: number;
    sha256: string;
  }> | null;
  visual_reviews: Array<Record<string, unknown>> | null;
}

export interface SubmissionCodeInputFile {
  id: number;
  original_filename: string;
  size_bytes: number;
  sha256: string;
}

export interface DetailItem {
  criterion: string;
  score: number;
  max_score?: number;
  comment: string;
  evidence?: string[];
}

export interface SubmissionDetail extends SubmissionOut {
  question_id?: string | null;
  ocr_text: string | null;
  question_ocr_text: string | null;
  feedback: string | null;
  assessment_suggestion: AiSuggestion | null;
  assessment_review: AssessmentReview | null;
  details: DetailItem[] | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  error_message: string | null;
  code_files: SubmissionCodeFile[];
  code_input_files: SubmissionCodeInputFile[];
}
// 轻量状态:处理中轮询用,字段集合刻意比 SubmissionOut 小
// 含 original_filename/uploaded_at 供 processing UI 显示,避免处理中拉完整详情
export type SubmissionStatusOut = components['schemas']['SubmissionStatusOut'];

export type SubmissionCreateResponse = components['schemas']['SubmissionCreateResponse'];

export type PaginatedSubmissions = components['schemas']['PaginatedSubmissions'];

export type FinalizePayload = components['schemas']['FinalizeRequest'];

export type BatchDeleteResponse = components['schemas']['BatchDeleteResponse'];

// 终态判断：awaiting_mcp 仍需要等待 MCP 客户端保存建议，不能停止状态刷新。
const TERMINAL_STATUSES: SubmissionStatus[] = [
  'ready_for_review',
  'reviewed',
  'failed',
];

export function isTerminal(status: SubmissionStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export function isProcessing(status: SubmissionStatus): boolean {
  return !isTerminal(status);
}

/**
 * awaiting_mcp 已经有可展示的作业详情，但仍等待 MCP 客户端写入建议。
 * 其他处理中状态的大字段尚未准备好，避免提前拉取完整详情。
 */
export function canLoadSubmissionDetail(status: SubmissionStatus): boolean {
  return status === 'awaiting_mcp' || isTerminal(status);
}
