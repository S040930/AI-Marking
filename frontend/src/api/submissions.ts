import { useQuery, useMutation } from '@tanstack/react-query';
import { apiClient } from './client';

// 类型定义
export type SubmissionStatus =
  | 'pending'
  | 'ocr_processing'
  | 'ocr_done'
  | 'llm_processing'
  | 'done'
  | 'failed';

export interface SubmissionOut {
  id: number;
  original_filename: string;
  status: SubmissionStatus;
  score: number | null;
  uploaded_at: string;
  completed_at: string | null;
}

export interface DetailItem {
  criterion: string;
  score: number;
  comment: string;
}

export interface SubmissionDetail extends SubmissionOut {
  ocr_text: string | null;
  feedback: string | null;
  details: DetailItem[] | null;
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
const TERMINAL_STATUSES: SubmissionStatus[] = ['done', 'failed'];

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
          params: { skip, limit: pageSize },
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

export function useSubmission(id: number | undefined) {
  return useQuery<SubmissionDetail>({
    queryKey: ['submission', id],
    queryFn: () =>
      apiClient.get<SubmissionDetail>(`/submissions/${id}`).then((r) => r.data),
    enabled: id !== undefined && !isNaN(id),
    // 非终态时每 2 秒轮询
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return isProcessing(data.status) ? 2000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useUploadSubmission() {
  return useMutation<SubmissionCreateResponse, Error, File>({
    mutationFn: (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      return apiClient
        .post<SubmissionCreateResponse>('/submissions', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          // mutation 错误由页面 onError 自行 toast，跳过拦截器自动提示
          skipErrorToast: true,
        })
        .then((r) => r.data);
    },
  });
}
