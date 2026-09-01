import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const mocks = vi.hoisted(() => ({
  retry: vi.fn(),
  remove: vi.fn(),
  exportResults: vi.fn(),
}));

vi.mock('@/api/submissions', () => ({
  useExportQuestionResults: () => ({
    mutate: mocks.exportResults,
    isPending: false,
  }),
}));

vi.mock('@/api/questions', () => ({
  useQuestions: () => ({
    isLoading: false,
    data: {
      total: 1,
      items: [
        {
          id: 'DTS208TC_CW1_Paper',
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
  useRenameQuestion: () => ({ mutate: vi.fn() }),
  useRetryQuestionOcr: () => ({ mutate: mocks.retry }),
  useDeleteQuestion: () => ({ mutate: mocks.remove, isPending: false }),
  useReplaceQuestion: () => ({ mutate: vi.fn(), isPending: false }),
  useChangeQuestionConfigProfile: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useGradingPrompt: () => ({ data: undefined, isLoading: true, isError: false }),
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

function renderPage() {
  return render(
    <MemoryRouter>
      <QuestionsPage />
    </MemoryRouter>,
  );
}

describe('QuestionsPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('提供前往题目上传页的入口', () => {
    renderPage();
    const link = screen.getByRole('link', { name: '上传题目' });
    expect(link).toHaveAttribute('href', '/questions/upload');
  });

  it('展示 OCR 失败原因并允许重新上传', () => {
    renderPage();
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
      { id: 'DTS208TC_CW1_Paper', file },
      expect.any(Object),
    );
  });

  it('必须输入完整题目名称才能执行危险删除', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    const action = screen.getByRole('button', { name: '确认并永久删除' });
    expect(action).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('输入完整题目名称'), {
      target: { value: '期末作文' },
    });
    expect(action).toBeEnabled();
  });

  it('按题目输入及格线并触发 Excel 导出', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '导出分析' }));
    const threshold = screen.getByLabelText('本次及格线（得分率 %）');
    expect(threshold).toHaveValue(60);
    fireEvent.change(threshold, { target: { value: '72.5' } });
    fireEvent.click(screen.getByRole('button', { name: '生成并下载 Excel' }));

    expect(mocks.exportResults).toHaveBeenCalledWith(
      {
        questionId: 'DTS208TC_CW1_Paper',
        passThreshold: 72.5,
        locale: 'zh-CN',
      },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
  });

  it('拒绝超出范围的及格线', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '导出分析' }));
    fireEvent.change(screen.getByLabelText('本次及格线（得分率 %）'), {
      target: { value: '101' },
    });

    expect(screen.getByText('请输入大于 0 且不超过 100 的数值')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成并下载 Excel' })).toBeDisabled();
  });
});
