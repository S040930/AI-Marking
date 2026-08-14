import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockMutate = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock('@/api/submissions', () => ({
  useUploadSubmission: () => ({ mutate: mockMutate, isPending: false }),
}));

vi.mock('@/api/questions', () => ({
  useCreateQuestion: () => ({ mutate: vi.fn(), isPending: false }),
  useQuestions: () => ({
    data: {
      items: [
        {
          id: 1,
          name: '期末作文',
          original_filename: 'question.pdf',
          status: 'ready',
          replacement_status: null,
          submission_count: 0,
        },
      ],
    },
  }),
}));

import UploadPage from '@/pages/UploadPage';

function renderWithQueryClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('UploadPage', () => {
  it('网页上传固定使用后端评分，不再显示 Codex 引擎选择', () => {
    renderWithQueryClient(<UploadPage />);

    expect(
      screen.getByRole('heading', { name: '上传作业' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('选择评分引擎')).not.toBeInTheDocument();
    expect(screen.queryByText('Codex GPT 评分')).not.toBeInTheDocument();
  });

  it('初始显示步骤 1：选择题目', () => {
    renderWithQueryClient(<UploadPage />);

    const stepper = screen.getByRole('navigation', { name: '步骤进度' });
    expect(within(stepper).getByText('选择题目')).toBeInTheDocument();
    expect(within(stepper).getByText('上传作业')).toBeInTheDocument();
    expect(within(stepper).getByText('确认提交')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('搜索题目')).toBeInTheDocument();
  });

  it('未选择题目时「下一步」禁用', () => {
    renderWithQueryClient(<UploadPage />);

    const nextButton = screen.getByRole('button', { name: '下一步' });
    expect(nextButton).toBeDisabled();
  });

  it('选择题目后可进入步骤 2', () => {
    renderWithQueryClient(<UploadPage />);

    const questionButton = screen.getByText('期末作文').closest('button');
    expect(questionButton).not.toBeNull();
    fireEvent.click(questionButton!);

    const nextButton = screen.getByRole('button', { name: '下一步' });
    expect(nextButton).toBeEnabled();

    fireEvent.click(nextButton);
    expect(screen.getByText('学生作业文件')).toBeInTheDocument();
  });

  it('步骤 2 未上传文件时「下一步」禁用', () => {
    renderWithQueryClient(<UploadPage />);

    fireEvent.click(screen.getByText('期末作文').closest('button')!);
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled();
  });

  it('步骤 2 上传文件后可进入步骤 3 并提交', async () => {
    renderWithQueryClient(<UploadPage />);

    fireEvent.click(screen.getByText('期末作文').closest('button')!);
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    const fileInput = screen.getByTestId('file-upload-input');
    const file = new File(['student answer'], 'answer.pdf', {
      type: 'application/pdf',
    });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText('answer.pdf')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(
      screen.getByRole('button', { name: '开始批改' }),
    ).toBeInTheDocument();
    expect(screen.getByText('期末作文')).toBeInTheDocument();
    expect(screen.getByText('answer.pdf')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '开始批改' }));
    expect(mockMutate).toHaveBeenCalledWith(
      { file, questionId: 1 },
      expect.any(Object),
    );
  });

  it('步骤 3 可返回修改并保留已选数据', async () => {
    renderWithQueryClient(<UploadPage />);

    fireEvent.click(screen.getByText('期末作文').closest('button')!);
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    const fileInput = screen.getByTestId('file-upload-input');
    const file = new File(['student answer'], 'answer.pdf', {
      type: 'application/pdf',
    });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText('answer.pdf')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    fireEvent.click(screen.getByRole('button', { name: '返回修改' }));

    expect(screen.getByText('学生作业文件')).toBeInTheDocument();
    expect(screen.getByText('answer.pdf')).toBeInTheDocument();
  });
});
