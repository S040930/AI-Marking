import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/api/client';
import { queryKeys } from '@/api/queryKeys';
import { useSubmission, useSubmissionStatus, useFinalizeSubmission, type SubmissionDetail } from '@/api/submissions';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
  constructor(_url: string) { FakeEventSource.instances.push(this); }
}

const initial = { id: 7, status: 'ready_for_review', grading_revision: 1,
  score: 10, max_score: 100, assessment_review: null } as SubmissionDetail;

function setup() {
  vi.stubGlobal('EventSource', FakeEventSource);
  const qc = new QueryClient({ defaultOptions: { queries: {
    staleTime: 30_000, retry: false, refetchOnWindowFocus: false,
  }, mutations: { retry: false } } });
  qc.setQueryData(queryKeys.submissions.status(7), initial);
  qc.setQueryData(queryKeys.submissions.detail(7, 'ready_for_review'), initial);
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { qc, wrapper };
}

function useReview() {
  const status = useSubmissionStatus(7);
  const detail = useSubmission(7, true, status.data?.status);
  return { status, detail };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  FakeEventSource.instances = [];
});

describe('ready_for_review live updates', () => {
  it('同状态 SSE 更新刷新建议，并保持订阅', async () => {
    vi.useFakeTimers();
    const { qc, wrapper } = setup();
    const latest = { ...initial, grading_revision: 2, score: 40 };
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: latest });
    const hook = renderHook(useReview, { wrapper });
    const source = FakeEventSource.instances[0];
    await act(async () => {
      source.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ submission_id: 7, status: 'ready_for_review' }) }));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(source.close).not.toHaveBeenCalled();
    expect(hook.result.current.detail.data?.grading_revision).toBe(2);
    hook.unmount(); qc.clear();
  });

  it('没有 SSE 通知时，30 秒刷新同版本独立复核', async () => {
    vi.useFakeTimers();
    const { qc, wrapper } = setup();
    const review = { verdict: 'agree', summary: 'Reviewed' };
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { ...initial, assessment_review: review } });
    const hook = renderHook(useReview, { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_100); });
    expect(get).toHaveBeenCalledWith('/submissions/7');
    expect(get).toHaveBeenCalledWith('/submissions/7/status');
    expect(hook.result.current.detail.data?.assessment_review).toEqual(review);
    hook.unmount(); qc.clear();
  });

  it('最终确认通知后停止观察及刷新', async () => {
    vi.useFakeTimers();
    const { qc, wrapper } = setup();
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { ...initial, status: 'reviewed' } });
    const hook = renderHook(useReview, { wrapper });
    const source = FakeEventSource.instances[0];
    await act(async () => {
      source.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ submission_id: 7, status: 'reviewed' }) }));
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(source.close).toHaveBeenCalled();
    expect(hook.result.current.status.data?.status).toBe('reviewed');
    get.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(get).not.toHaveBeenCalled();
    hook.unmount(); qc.clear();
  });

  it('提交 409 后重新读取当前状态和详情', async () => {
    vi.useFakeTimers();
    const { qc, wrapper } = setup();
    const latest = { ...initial, grading_revision: 2, score: 40 };
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: latest });
    const conflict = new AxiosError('Conflict', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 409, statusText: 'Conflict', data: { detail: { current_revision: 2 } },
      headers: {}, config: { headers: new AxiosHeaders() },
    });
    vi.spyOn(apiClient, 'post').mockRejectedValue(conflict);
    const hook = renderHook(() => ({ ...useReview(), finalize: useFinalizeSubmission(7) }), { wrapper });
    const payload = { expected_revision: 1, reviewer_name: 'Teacher', score: 10, max_score: 100,
      feedback: 'Draft', details: [{ criterion: 'Task', score: 10, max_score: 100, comment: 'Draft' }] };
    await act(async () => {
      await hook.result.current.finalize.mutateAsync(payload).catch(() => {});
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(get).toHaveBeenCalledWith('/submissions/7');
    expect(get).toHaveBeenCalledWith('/submissions/7/status');
    expect(hook.result.current.detail.data?.grading_revision).toBe(2);
    expect(hook.result.current.finalize.isError).toBe(true);
    hook.unmount(); qc.clear();
  });
});
