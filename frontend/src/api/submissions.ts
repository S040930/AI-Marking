import { useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

// 类型定义
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
    // 历史列表只承担状态摘要展示，低频刷新避免放大数据库压力。
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return data.items.some((s) => isProcessing(s.status)) ? 15000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

/**
 * 提交总数(独立 query,不随列表轮询)。
 *
 * 拆分原因:list 接口在处理中时每 15s 轮询,若每次都算 COUNT(*) 会产生
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
 * 完整详情查询。仅在 ``enabled=true`` 时拉取(含 ocr_text/assessment_suggestion 大字段)。
 *
 * P2-L3:调用方应根据轻量 status 判断是否终态,处理中传 ``enabled=false``
 * 避免拉取大字段(此时大字段为 null,属于浪费)。终态后再 enable 拉取。
 */
export function useSubmission(
  id: number | undefined,
  enabled: boolean = true,
  status?: SubmissionStatus,
) {
  return useQuery<SubmissionDetail>({
    // 状态变化时切换详情 key，避免 fallback 轮询更新 status 后仍复用
    // 旧状态的建议；同时保留 ['submission', id] 前缀供 SSE 失效缓存。
    queryKey: ['submission', id, status ?? 'detail'],
    queryFn: () =>
      apiClient.get<SubmissionDetail>(`/submissions/${id}`).then((r) => r.data),
    enabled: id !== undefined && !isNaN(id) && enabled,
    refetchInterval: false,
    refetchIntervalInBackground: false,
  });
}

/**
 * SSE 订阅:监听 ``/submissions/{id}/events`` 推送的状态变更事件。
 *
 * 收到事件后立即 invalidate 对应 status query,触发 ``useSubmissionStatus``
 * 重新拉取轻量状态。终态事件额外 invalidate 完整详情 query,触发
 * ``useSubmission`` 拉取 ``SubmissionDetail``。
 *
 * 与 ``useSubmissionStatus`` 的 30s 兜底轮询配合:SSE 主路径推送,
 * 轮询仅在 SSE 断开时(网络抖动、代理超时)仍能恢复。
 */
export function useSubmissionEvents(id: number | undefined) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (id === undefined || isNaN(id)) return;
    // EventSource 在 SSR / 部分测试环境不存在,守卫一下避免崩溃
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') {
      return;
    }
    const es = new EventSource(`/api/submissions/${id}/events`);
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as {
          submission_id?: number;
          status?: SubmissionStatus;
        };
        if (data.submission_id !== id) return;
        queryClient.invalidateQueries({
          queryKey: ['submission-status', id],
        });
        if (data.status && isTerminal(data.status)) {
          // 终态:刷新完整详情,供 ResultPage 渲染 ocr_text/feedback 等
          queryClient.invalidateQueries({ queryKey: ['submission', id] });
        }
      } catch {
        // ignore parse errors(包括 keepalive 注释行,虽然 EventSource 不会
        // 把注释行投递到 onmessage,但兜底防御)
      }
    };
    // EventSource 自带断线重连,无需手动处理 onerror;
    // 显式 onerror=null 让浏览器走默认重连逻辑,避免控制台噪音。
    es.onerror = null;
    return () => {
      es.close();
    };
  }, [id, queryClient]);
}

/**
 * 轻量状态查询:主路径靠 SSE 推送触发 invalidate,30s 兜底轮询。
 *
 * 进入终态后停止轮询。调用方在终态下应当用 ``useSubmission(id)`` 拉
 * 完整 ``SubmissionDetail`` 用于结果渲染。
 */
export function useSubmissionStatus(id: number | undefined) {
  useSubmissionEvents(id);
  return useQuery<SubmissionStatusOut>({
    queryKey: ['submission-status', id],
    queryFn: () =>
      apiClient
        .get<SubmissionStatusOut>(`/submissions/${id}/status`)
        .then((r) => r.data),
    enabled: id !== undefined && !isNaN(id),
    // 兜底轮询:SSE 断开时仍能更新;30s 远小于原 2s 的请求量
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return isProcessing(data.status) ? 30000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

export interface UploadSubmissionPayload {
  file: File;
  questionId: number;
  codeFiles?: File[];
  codeManifest?: Array<{ filename: string; question_number: number }>;
}

export function useUploadSubmission() {
  const queryClient = useQueryClient();
  return useMutation<SubmissionCreateResponse, Error, UploadSubmissionPayload>({
    mutationFn: ({ file, questionId, codeFiles = [], codeManifest }) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('question_id', String(questionId));
      codeFiles.forEach((codeFile) => formData.append('code_files', codeFile));
      if (codeManifest) formData.append('code_manifest', JSON.stringify(codeManifest));
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

export function useRetrySubmission(submissionId: number) {
  const queryClient = useQueryClient();
  return useMutation<SubmissionCreateResponse, Error, File | undefined>({
    mutationFn: (file) => {
      const form = new FormData();
      if (file) form.append('file', file);
      return apiClient
        .post<SubmissionCreateResponse>(
          `/submissions/${submissionId}/retry`,
          form,
          {
            headers: { 'Content-Type': 'multipart/form-data' },
            skipErrorToast: true,
          },
        )
        .then((response) => response.data);
    },
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ['submission', submissionId] });
      queryClient.invalidateQueries({
        queryKey: ['submission-status', submissionId],
      });
      queryClient.invalidateQueries({ queryKey: ['submissions'] });
    },
  });
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

export interface BatchDeleteResponse {
  deleted_count: number;
}

export function useBatchDeleteSubmissions() {
  const queryClient = useQueryClient();
  return useMutation<BatchDeleteResponse, Error, number[]>({
    mutationFn: (ids) =>
      apiClient
        .delete<BatchDeleteResponse>('/submissions', {
          data: { ids },
          skipErrorToast: true,
        })
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['submissions'] });
      queryClient.invalidateQueries({ queryKey: ['submissions-count'] });
    },
  });
}
