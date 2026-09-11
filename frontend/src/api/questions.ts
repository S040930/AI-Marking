import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/generated';
import { subscribeToQuestionEvents } from '@/api/questionEvents';
import { queryKeys } from '@/api/queryKeys';

export type Question = components['schemas']['QuestionOut'];

export function useGradingPrompt(questionId: string | null) {
  return useQuery({
    queryKey: queryKeys.questions.gradingPrompt(questionId),
    queryFn: () =>
      apiClient
        .get<components['schemas']['GradingPromptOut']>(`/questions/${questionId}/grading-prompt`, {
          skipErrorToast: true,
        })
        .then((response) => response.data),
    enabled: questionId !== null,
    staleTime: 60_000,
  });
}

export function useQuestions(search = '', limit = 50) {
  const queryClient = useQueryClient();
  useEffect(
    () => subscribeToQuestionEvents(() => {
      queryClient.invalidateQueries({ queryKey: queryKeys.questions.all });
    }),
    [queryClient],
  );
  return useQuery({
    queryKey: queryKeys.questions.list(search, limit),
    queryFn: () =>
      apiClient
        .get<components['schemas']['PaginatedQuestions']>('/questions', {
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
        ? 30000
        : false,
  });
}

// 轮询单个题目的状态，直到 OCR 识别结束（ready / failed）或超过 maxPolls 次。
// 用于上传题目后在本页等待识别完成再进入下一步。
export function useQuestionWatch(questionId: string | null, maxPolls = 120) {
  const queryClient = useQueryClient();
  useEffect(
    () => subscribeToQuestionEvents(() => {
      queryClient.invalidateQueries({ queryKey: queryKeys.questions.watch(questionId) });
    }),
    [queryClient, questionId],
  );
  return useQuery<Question | null>({
    queryKey: queryKeys.questions.watch(questionId),
    queryFn: () =>
      apiClient
        .get<components['schemas']['QuestionDetail']>(`/questions/${questionId}`, {
          skipErrorToast: true,
        })
        .then((response) => response.data as Question),
    enabled: questionId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'ready' || status === 'failed') return false;
      // 超过 maxPolls 次（约 maxPolls × 3 秒）后停止轮询，避免无限等待
      if (query.state.dataUpdateCount + query.state.fetchFailureCount >= maxPolls) {
        return false;
      }
      return 3000;
    },
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.questions.all }),
  });
}

export function useRenameQuestion() {
  const queryClient = useQueryClient();
  return useMutation<Question, Error, { id: string; name: string }>({
    mutationFn: ({ id, name }) =>
      apiClient
        .patch<Question>(`/questions/${id}`, { name })
        .then((response) => response.data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.questions.all }),
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.questions.all }),
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
      queryClient.invalidateQueries({ queryKey: queryKeys.questions.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.count });
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
      queryClient.invalidateQueries({ queryKey: queryKeys.questions.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.count });
    },
  });
}
