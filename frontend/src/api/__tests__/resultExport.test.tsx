import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  apiClient: { get: mocks.get },
}));

import { useExportQuestionResults } from '@/api/submissions';

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

describe('useExportQuestionResults', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:test-export'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
  });

  it('请求工作簿并使用响应文件名下载 Blob', async () => {
    mocks.get.mockResolvedValue({
      data: new Blob(['xlsx']),
      headers: {
        'content-disposition':
          'attachment; filename="grade_analysis_test.xlsx"',
      },
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});
    const { result } = renderHook(() => useExportQuestionResults(), {
      wrapper,
    });

    await act(async () => {
      await result.current.mutateAsync({
        questionId: 'question-1',
        passThreshold: 65,
        locale: 'en-US',
      });
    });

    expect(mocks.get).toHaveBeenCalledWith('/submissions/export.xlsx', {
      params: {
        question_id: 'question-1',
        pass_threshold: 65,
        locale: 'en-US',
      },
      responseType: 'blob',
      timeout: 120_000,
      skipErrorToast: true,
    });
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-export');
  });
});
