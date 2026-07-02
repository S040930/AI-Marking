import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

// 类型定义
export type SubmissionStatus =
  | 'pending'
  | 'ocr_processing'
  | 'ocr_done'
  | 'agent_grading'
  | 'agent_reviewing'
  | 'agent_revising'
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
  outcome?: string;
  review_reason?: string | null;
  critic_summary?: string;
  critic_issues?: string[];
}

export interface SubmissionOut {
  id: number;
  original_filename: string;
  question_original_filename: string | null;
  status: SubmissionStatus;
  score: number | null;
  max_score: number | null;
  confidence: number | null;
  ai_suggestion: AiSuggestion | null;
  uploaded_at: string;
  completed_at: string | null;
}

export interface DetailItem {
  criterion: string;
  score: number;
  max_score?: number;
  comment: string;
  evidence?: string[];
}

/**
 * AI 建议评分(协同评分页用)。由 AI 自动产出的建议分数/反馈/明细,
 * 教师可在 ReviewPage 中据此调整后提交最终评分。
 */
export type AISuggestion = AiSuggestion;

export interface AgentTraceEvent {
  node: string;
  status: string;
  attempt: number;
  summary: string;
  timestamp: string;
  duration_ms: number;
}

export interface SubmissionDetail extends SubmissionOut {
  ocr_text: string | null;
  question_ocr_text: string | null;
  feedback: string | null;
  details: DetailItem[] | null;
  ai_result: Record<string, unknown> | null;
  agent_trace: AgentTraceEvent[] | null;
  review_reason: string | null;
  reviewed_by: string | null;
  review_note: string | null;
  reviewed_at: string | null;
  error_message: string | null;
}

// 轻量状态:处理中轮询用,字段集合刻意比 SubmissionOut 小
// 含 original_filename/uploaded_at 供 processing UI 显示,避免处理中拉完整详情
export interface SubmissionStatusOut {
  id: number;
  status: SubmissionStatus;
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

// 终态判断
const TERMINAL_STATUSES: SubmissionStatus[] = ['ready_for_review', 'reviewed', 'failed'];

export function isTerminal(status: SubmissionStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export function isProcessing(status: SubmissionStatus): boolean {
  return !isTerminal(status);
}

// hooks
export function useSubmissions({ page, pageSize }: { page: number; pageSize: number }) {
  const skip = (page - 1) * pageSize;
  return useQuery<PaginatedSubmissions>({
    queryKey: ['submissions', { page, pageSize }],
    queryFn: () =>
      apiClient
        .get<PaginatedSubmissions>('/submissions', {
          // 轮询场景跳过 count 查询,总数由独立的 useSubmissionsCount 提供
          params: { skip, limit: pageSize, include_count: false },
        })
        .then((r) => r.data),
    placeholderData: (prev) => prev,
    // 存在非终态记录时每 3 秒轮询
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return data.items.some((s) => isProcessing(s.status)) ? 3000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

/**
 * 提交总数(独立 query,不随列表轮询)。
 *
 * 拆分原因:list 接口在处理中时每 3s 轮询,若每次都算 COUNT(*) 会产生
 * 无谓 DB 往返。总数只在翻页/首次加载时需要,用 staleTime 30s 平滑缓存。
 */
export function useSubmissionsCount() {
  return useQuery<{ total: number }>({
    queryKey: ['submissions-count'],
    queryFn: () =>
      apiClient.get<{ total: number }>('/submissions/count').then((r) => r.data),
    staleTime: 30_000,
    refetchInterval: false,
    refetchIntervalInBackground: false,
  });
}

/**
 * 完整详情查询。仅在 ``enabled=true`` 时拉取(含 ocr_text/ai_result 大字段)。
 *
 * P2-L3:调用方应根据轻量 status 判断是否终态,处理中传 ``enabled=false``
 * 避免拉取大字段(此时大字段为 null,属于浪费)。终态后再 enable 拉取。
 */
export function useSubmission(id: number | undefined, enabled: boolean = true) {
  return useQuery<SubmissionDetail>({
    queryKey: ['submission', id],
    queryFn: () =>
      apiClient.get<SubmissionDetail>(`/submissions/${id}`).then((r) => r.data),
    enabled: id !== undefined && !isNaN(id) && enabled,
    refetchInterval: false,
    refetchIntervalInBackground: false,
  });
}

/**
 * 轻量状态轮询:处理中每 2 秒拉取 ``/submissions/{id}/status``.
 *
 * 进入终态后停止轮询,调用方通常应当用 ``useSubmission(id)`` 再拉一次
 * 完整 ``SubmissionDetail`` 用于结果渲染。
 */
export function useSubmissionStatus(id: number | undefined) {
  return useQuery<SubmissionStatusOut>({
    queryKey: ['submission-status', id],
    queryFn: () =>
      apiClient
        .get<SubmissionStatusOut>(`/submissions/${id}/status`)
        .then((r) => r.data),
    enabled: id !== undefined && !isNaN(id),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return isProcessing(data.status) ? 2000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

export interface UploadSubmissionPayload {
  file: File;
  questionFile: File;
}

export function useUploadSubmission() {
  const queryClient = useQueryClient();
  return useMutation<SubmissionCreateResponse, Error, UploadSubmissionPayload>({
    mutationFn: ({ file, questionFile }) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('question_file', questionFile);
      return apiClient
        .post<SubmissionCreateResponse>('/submissions', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          // mutation 错误由页面 onError 自行 toast，跳过拦截器自动提示
          skipErrorToast: true,
        })
        .then((r) => r.data);
    },
    onSuccess: () => {
      // 上传成功后失效总数缓存,下次进入历史页重新拉取
      queryClient.invalidateQueries({ queryKey: ['submissions-count'] });
    },
  });
}

export interface ConversationMessage {
  id: number;
  submission_id: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ChatPayload {
  message: string;
}

export interface ChatResponse {
  reply: string;
  message_id: number;
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

export function useConversations(submissionId: number | undefined) {
  return useQuery<ConversationMessage[]>({
    queryKey: ['conversations', submissionId],
    queryFn: () =>
      apiClient
        .get<ConversationMessage[]>(`/submissions/${submissionId}/conversations`)
        .then((r) => r.data),
    enabled: submissionId !== undefined && !isNaN(submissionId),
    refetchInterval: false,
  });
}

export function useChat(submissionId: number) {
  const queryClient = useQueryClient();
  return useMutation<
    ChatResponse,
    Error,
    ChatPayload,
    { previous: ConversationMessage[] | undefined }
  >({
    mutationFn: (payload) =>
      apiClient
        .post<ChatResponse>(`/submissions/${submissionId}/chat`, payload, {
          skipErrorToast: true,
        })
        .then((r) => r.data),
    onMutate: async (payload) => {
      // 乐观更新:先在 UI 显示教师消息,成功后再由 onSuccess 刷新完整列表
      await queryClient.cancelQueries({
        queryKey: ['conversations', submissionId],
      });
      const previous = queryClient.getQueryData<ConversationMessage[]>([
        'conversations',
        submissionId,
      ]);
      const optimistic: ConversationMessage = {
        id: Date.now(),
        submission_id: submissionId,
        role: 'user',
        content: payload.message,
        created_at: new Date().toISOString(),
      };
      queryClient.setQueryData<ConversationMessage[]>(
        ['conversations', submissionId],
        (old) => [...(old ?? []), optimistic],
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      // 回滚乐观更新,由调用方 onError 自行 toast
      if (context?.previous) {
        queryClient.setQueryData(
          ['conversations', submissionId],
          context.previous,
        );
      }
    },
    onSuccess: () => {
      // AI 回复后刷新对话列表(替换乐观消息为服务端真实数据)
      queryClient.invalidateQueries({
        queryKey: ['conversations', submissionId],
      });
    },
  });
}

export function useFinalizeSubmission(submissionId: number) {
  const queryClient = useQueryClient();
  return useMutation<SubmissionDetail, Error, FinalizePayload>({
    mutationFn: (payload) =>
      apiClient
        .post<SubmissionDetail>(`/submissions/${submissionId}/finalize`, payload, {
          skipErrorToast: true,
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(['submission', submissionId], data);
      queryClient.invalidateQueries({ queryKey: ['submissions'] });
      queryClient.invalidateQueries({ queryKey: ['submissions-count'] });
    },
  });
}
