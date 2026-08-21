// 作业提交相关类型定义与纯状态判断函数。
// hooks 与请求逻辑在 ./submissions.ts 中,并通过 re-export 保持统一出口。

export type SubmissionStatus =
  | 'pending'
  | 'ocr_processing'
  | 'ocr_done'
  | 'awaiting_mcp'
  | 'ready_for_review'
  | 'reviewed'
  | 'failed';

export interface AiSuggestionDetail {
  criterion: string;
  score: number;
  max_score: number;
  comment: string;
  evidence?: string[];
}

export interface AiSuggestion {
  score: number;
  max_score: number;
  feedback: string;
  details: AiSuggestionDetail[];
  confidence: number;
  mcp_metadata?: {
    client?: string;
    generated_at?: string;
    [key: string]: unknown;
  };
}

export interface AssessmentReviewItem {
  rubric_item_id: string;
  criterion: string;
  max_score: number;
  verdict: 'agree' | 'disagree';
  comment: string;
  suggested_score: number | null;
}

export interface AssessmentReview {
  verdict: 'agree' | 'partial' | 'disagree';
  summary: string;
  confidence: number;
  items: AssessmentReviewItem[];
  // 被复核的 grading_revision；与当前 revision 不一致时视为过期
  reviewed_revision: number;
  client?: string | null;
  created_at: string;
}

export interface SubmissionOut {
  id: number;
  original_filename: string;
  question_original_filename: string | null;
  status: SubmissionStatus;
  grading_mode: 'external_agent';
  grading_revision: number;
  graded_at: string | null;
  score: number | null;
  max_score: number | null;
  confidence: number | null;
  uploaded_at: string;
  completed_at: string | null;
  has_code?: boolean;
}

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
export interface SubmissionStatusOut {
  id: number;
  status: SubmissionStatus;
  grading_mode: 'external_agent';
  grading_revision: number;
  original_filename: string;
  score: number | null;
  max_score: number | null;
  confidence: number | null;
  uploaded_at: string;
  completed_at: string | null;
  error_message: string | null;
}

export interface SubmissionCreateResponse {
  id: number;
  status: SubmissionStatus;
}

export interface PaginatedSubmissions {
  items: SubmissionOut[];
  total: number;
  skip: number;
  limit: number;
}

export interface FinalizePayload {
  reviewer_name: string;
  score: number;
  max_score: number;
  feedback: string;
  details: Array<{
    criterion: string;
    score: number;
    max_score: number;
    comment: string;
    evidence: string[];
  }>;
}

export interface BatchDeleteResponse {
  deleted_count: number;
}

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
