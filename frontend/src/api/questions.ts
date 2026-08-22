import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';

export type QuestionStatus = 'pending' | 'ocr_processing' | 'ready' | 'failed';
export type QuestionReplacementStatus = 'pending' | 'processing' | 'failed';

export interface Question {
  id: string;
  config_profile_id: number;
  name: string;
  original_filename: string;
  status: QuestionStatus;
  error_message: string | null;
  replacement_status: QuestionReplacementStatus | null;
  replacement_error_message: string | null;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  submission_count: number;
}

export interface PaginatedQuestions {
  items: Question[];
  total: number;
  skip: number;
  limit: number;
}

/** GET /questions/{id}/grading-prompt 返回的提示词素材（后端生成，与评分包同源）。 */
export interface GradingPrompt {
  question_id: string;
  name: string;
  grading_mode: string;
  review_enabled: boolean;
  source: string;
  snapshot_id: string | null;
  total_max_score: number;
  needs_rubric: boolean;
  ocr_text: string | null;
  grading_policy: Record<string, unknown>;
  text: string;
}

export function useGradingPrompt(questionId: string | null) {
  return useQuery({
    queryKey: ['questions', questionId, 'grading-prompt'],
    queryFn: () =>
      apiClient
        .get<GradingPrompt>(`/questions/${questionId}/grading-prompt`, {
          skipErrorToast: true,
        })
        .then((response) => response.data),
    enabled: questionId !== null,
    staleTime: 60_000,
  });
}

export function useQuestions(search = '', limit = 50) {
  return useQuery({
    queryKey: ['questions', { search, limit }],
    queryFn: () =>
      apiClient
        .get<PaginatedQuestions>('/questions', {
          params: { search, limit, skip: 0 },
        })
        .then((response) => response.data),
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (item) => item.status === 'pending' || item.status === 'ocr_processing',
      ) ||
      query.state.data?.items.some(
        (item) =>
          item.replacement_status === 'pending' ||
          item.replacement_status === 'processing',
      )
        ? 2000
        : false,
  });
}

export function useCreateQuestion() {
  const queryClient = useQueryClient();
  return useMutation<Question, Error, { file: File; name?: string; configProfileId?: number }>({
    mutationFn: ({ file, name, configProfileId }) => {
      const form = new FormData();
      form.append('file', file);
      if (name) form.append('name', name);
      if (configProfileId) form.append('config_profile_id', String(configProfileId));
      return apiClient
        .post<Question>('/questions', form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          skipErrorToast: true,
        })
        .then((response) => response.data);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['questions'] }),
  });
}

export function useRenameQuestion() {
  const queryClient = useQueryClient();
  return useMutation<Question, Error, { id: string; name: string }>({
    mutationFn: ({ id, name }) =>
      apiClient
        .patch<Question>(`/questions/${id}`, { name })
        .then((response) => response.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['questions'] }),
  });
}

export function useRetryQuestionOcr() {
  const queryClient = useQueryClient();
  return useMutation<Question, Error, { id: string; file: File }>({
    mutationFn: ({ id, file }) => {
      const form = new FormData();
      form.append('file', file);
      return (
      apiClient
        .post<Question>(`/questions/${id}/retry-ocr`, form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          skipErrorToast: true,
        })
        .then((response) => response.data)
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['questions'] }),
  });
}

export function useChangeQuestionConfigProfile() {
  const queryClient = useQueryClient();
  return useMutation<Question, Error, { id: string; configProfileId: number }>({
    mutationFn: ({ id, configProfileId }) =>
      apiClient
        .patch<Question>(`/questions/${id}/config-profile`, {
          config_profile_id: configProfileId,
        })
        .then((response) => response.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['questions'] }),
  });
}

export function useDeleteQuestion() {
  const queryClient = useQueryClient();
  return useMutation<
    { deleted_submission_count: number },
    Error,
    { id: string; confirmationName: string }
  >({
    mutationFn: ({ id, confirmationName }) =>
      apiClient
        .delete(`/questions/${id}`, {
          data: { confirmation_name: confirmationName },
          skipErrorToast: true,
        })
        .then((response) => response.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['questions'] });
      queryClient.invalidateQueries({ queryKey: ['submissions'] });
      queryClient.invalidateQueries({ queryKey: ['submissions-count'] });
    },
  });
}

export function useReplaceQuestion() {
  const queryClient = useQueryClient();
  return useMutation<
    {
      question_id: string;
      replacement_status: 'pending';
      affected_submission_count: number;
    },
    Error,
    { id: string; file: File; confirmationName: string; acknowledgeDeletion: boolean }
  >({
    mutationFn: ({ id, file, confirmationName, acknowledgeDeletion }) => {
      const form = new FormData();
      form.append('file', file);
      form.append('confirmation_name', confirmationName);
      form.append('acknowledge_deletion', String(acknowledgeDeletion));
      return apiClient
        .post(`/questions/${id}/replace`, form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 660_000,
          skipErrorToast: true,
        })
        .then((response) => response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['questions'] });
      queryClient.invalidateQueries({ queryKey: ['submissions'] });
      queryClient.invalidateQueries({ queryKey: ['submissions-count'] });
    },
  });
}
