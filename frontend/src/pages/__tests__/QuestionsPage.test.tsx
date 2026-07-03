import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  retry: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('@/api/questions', () => ({
  useQuestions: () => ({
    isLoading: false,
    data: {
      total: 1,
      items: [
        {
          id: 7,
          name: '期末作文',
          original_filename: 'essay.pdf',
          status: 'failed',
          error_message: 'OCR 服务异常',
          submission_count: 3,
          created_at: '2026-07-03T10:00:00',
          updated_at: '2026-07-03T10:00:00',
          last_used_at: null,
        },
      ],
    },
  }),
  useCreateQuestion: () => ({ mutate: vi.fn(), isPending: false }),
  useRenameQuestion: () => ({ mutate: vi.fn() }),
  useRetryQuestionOcr: () => ({ mutate: mocks.retry }),
  useDeleteQuestion: () => ({ mutate: mocks.remove, isPending: false }),
  useReplaceQuestion: () => ({ mutate: vi.fn(), isPending: false }),
}));

import QuestionsPage from '@/pages/QuestionsPage';

describe('QuestionsPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('展示 OCR 失败原因并允许重试', () => {
    render(<QuestionsPage />);
    expect(screen.getByText('OCR 服务异常')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新识别' }));
    expect(mocks.retry).toHaveBeenCalledWith(7);
  });

  it('必须输入完整题目名称才能执行危险删除', () => {
    render(<QuestionsPage />);
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    const action = screen.getByRole('button', { name: '确认并永久删除' });
    expect(action).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('输入完整题目名称'), {
      target: { value: '期末作文' },
    });
    expect(action).toBeEnabled();
  });
});
