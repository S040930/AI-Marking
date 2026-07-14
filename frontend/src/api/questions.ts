import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';

export type QuestionStatus = 'pending' | 'ocr_processing' | 'ready' | 'failed';
export type QuestionReplacementStatus = 'pending' | 'processing' | 'failed';

export interface Question {
  id: number;
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
  return useMutation<Question, Error, { file: File; name?: string }>({
    mutationFn: ({ file, name }) => {
      const form = new FormData();
      form.append('file', file);
      if (name) form.append('name', name);
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
  return useMutation<Question, Error, { id: number; name: string }>({
    mutationFn: ({ id, name }) =>
      apiClient
        .patch<Question>(`/questions/${id}`, { name })
        .then((response) => response.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['questions'] }),
  });
}

export function useRetryQuestionOcr() {
  const queryClient = useQueryClient();
  return useMutation<Question, Error, { id: number; file: File }>({
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

export function useDeleteQuestion() {
  const queryClient = useQueryClient();
  return useMutation<
    { deleted_submission_count: number },
    Error,
    { id: number; confirmationName: string }
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
      question_id: number;
      replacement_status: 'pending';
      affected_submission_count: number;
    },
    Error,
    { id: number; file: File; confirmationName: string; acknowledgeDeletion: boolean }
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
