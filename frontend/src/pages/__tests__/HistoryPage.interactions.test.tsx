import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  refetchSubmissions: vi.fn(),
  refetchCount: vi.fn(),
  mutate: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: {
    error: mocks.toastError,
    success: mocks.toastSuccess,
  },
}));

vi.mock('@/api/submissions', () => ({
  isProcessing: (status: string) =>
    !['ready_for_review', 'reviewed', 'failed'].includes(status),
  useSubmissions: () => ({
    data: {
      items: [
        {
          id: 1,
          original_filename: 'processing.pdf',
          question_original_filename: 'question.pdf',
          status: 'agent_grading',
          score: null,
          max_score: null,
          confidence: null,
          uploaded_at: '2026-07-03T10:00:00',
          completed_at: null,
        },
        {
          id: 2,
          original_filename: 'reviewed.pdf',
          question_original_filename: 'question.pdf',
          status: 'reviewed',
          score: 90,
          max_score: 100,
          confidence: 0.9,
          uploaded_at: '2026-07-03T10:01:00',
          completed_at: '2026-07-03T10:02:00',
        },
      ],
      total: 2,
      skip: 0,
      limit: 10,
    },
    isLoading: false,
    isPlaceholderData: false,
    refetch: mocks.refetchSubmissions,
  }),
  useSubmissionsCount: () => ({
    data: { total: 2 },
    refetch: mocks.refetchCount,
  }),
  useBatchDeleteSubmissions: () => ({
    mutate: mocks.mutate,
    isPending: false,
  }),
}));

import HistoryPage from '@/pages/HistoryPage';

describe('HistoryPage 删除选择', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('处理中记录不可选择，终态记录可以选择', () => {
    render(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    );

    expect(
      screen.getByLabelText('processing.pdf 批改完成后方可删除'),
    ).toBeDisabled();
    expect(screen.getByLabelText('选择 reviewed.pdf')).toBeEnabled();
  });

  it('全选当前页只选择允许删除的终态记录', () => {
    render(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('全选当前页'));

    expect(screen.getByLabelText('选择 reviewed.pdf')).toBeChecked();
    expect(screen.getByText('已选 1 项')).toBeInTheDocument();
    expect(
      screen.getByLabelText('processing.pdf 批改完成后方可删除'),
    ).toBeDisabled();
  });

  it('后端返回 409 时保留选择、刷新列表并显示明确提示', () => {
    mocks.mutate.mockImplementation(
      (
        _ids: number[],
        options: { onError: (error: unknown) => void },
      ) => {
        options.onError({
          isAxiosError: true,
          response: {
            status: 409,
            data: {
              detail: {
                message: '正在处理的记录不可删除，请等待批改完成后重试',
                blocked_ids: [2],
              },
            },
          },
        });
      },
    );
    render(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('选择 reviewed.pdf'));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));

    expect(mocks.toastError).toHaveBeenCalledWith(
      '正在处理的记录不可删除，请等待批改完成后重试',
    );
    expect(mocks.refetchSubmissions).toHaveBeenCalled();
    expect(mocks.refetchCount).toHaveBeenCalled();
    expect(screen.getByText('已选 1 项')).toBeInTheDocument();
  });
});
