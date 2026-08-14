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
          config_profile_id: 1,
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
  useChangeQuestionConfigProfile: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

vi.mock('@/api/config', () => ({
  useConfigProfiles: () => ({
    data: [
      { id: 1, name: '默认配置', is_default: true },
      { id: 2, name: '初二语文', is_default: false },
    ],
  }),
}));

import QuestionsPage from '@/pages/QuestionsPage';

describe('QuestionsPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('展示 OCR 失败原因并允许重新上传', () => {
    render(<QuestionsPage />);
    expect(screen.getByText(/OCR 服务异常/)).toBeInTheDocument();
    const file = new File(['pdf'], 'replacement.pdf', {
      type: 'application/pdf',
    });
    const input = screen.getByLabelText('重新上传 期末作文 文件');
    expect(input).toHaveAttribute('accept', expect.stringContaining('.pdf'));
    fireEvent.change(input, {
      target: { files: [file] },
    });
    expect(mocks.retry).toHaveBeenCalledWith(
      { id: 7, file },
      expect.any(Object),
    );
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
