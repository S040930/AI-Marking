import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { LanguageProvider } from '@/i18n';

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  shouldFail: false,
}));

vi.mock('@/api/questions', () => ({
  useCreateQuestion: () => ({ mutate: mocks.mutate, isPending: false }),
}));

vi.mock('@/api/config', () => ({
  useConfigProfiles: () => ({
    data: [
      { id: 1, name: '默认配置', is_default: true },
      { id: 2, name: '初二语文', is_default: false },
    ],
  }),
}));

import UploadQuestionsPage from '@/pages/UploadQuestionsPage';

function renderPage() {
  return render(
    <LanguageProvider>
      <MemoryRouter>
        <UploadQuestionsPage />
      </MemoryRouter>
    </LanguageProvider>,
  );
}

function selectFile(name: string) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(['pdf'], name, { type: 'application/pdf' });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

describe('UploadQuestionsPage', () => {
  beforeEach(() => {
    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: 'zh-CN',
    });
    mocks.shouldFail = false;
    mocks.mutate.mockReset();
    mocks.mutate.mockImplementation((_vars, opts) => {
      if (mocks.shouldFail) {
        opts?.onError?.(new Error('OCR 服务异常'));
      } else {
        opts?.onSuccess?.({ id: 1, name: _vars.name, status: 'pending' });
      }
    });
  });

  it('展示上传页标题、配置项目选择与上传按钮', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: '上传题目' })).toBeInTheDocument();
    expect(screen.getByLabelText('上传题目使用的配置项目')).toHaveValue('1');
    const uploadButton = screen.getByRole('button', { name: '上传' });
    expect(uploadButton).toBeDisabled();
  });

  it('选择 PDF 后显示文件行并禁用上传按钮解除', () => {
    renderPage();
    selectFile('essay.pdf');
    const nameInput = screen.getByLabelText('题目名称 1');
    expect(nameInput).toHaveValue('essay');
    expect(screen.getByRole('button', { name: '上传' })).toBeEnabled();
  });

  it('上传成功后将条目标记为已上传并提示识别', () => {
    renderPage();
    selectFile('essay.pdf');
    fireEvent.click(screen.getByRole('button', { name: '上传' }));
    expect(mocks.mutate).toHaveBeenCalledWith(
      { file: expect.any(File), name: 'essay', configProfileId: 1 },
      expect.any(Object),
    );
    expect(screen.getByText('已上传，识别中')).toBeInTheDocument();
  });

  it('上传失败后显示错误并提供重试', () => {
    renderPage();
    selectFile('essay.pdf');
    mocks.shouldFail = true;
    fireEvent.click(screen.getByRole('button', { name: '上传' }));
    expect(screen.getByText('OCR 服务异常')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();

    mocks.shouldFail = false;
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(screen.getByText('已上传，识别中')).toBeInTheDocument();
  });

  it('空名称会提示错误', () => {
    renderPage();
    selectFile('essay.pdf');
    const nameInput = screen.getByLabelText('题目名称 1');
    fireEvent.change(nameInput, { target: { value: '  ' } });
    fireEvent.click(screen.getByRole('button', { name: '上传' }));
    expect(screen.getByText('题目名称不能为空')).toBeInTheDocument();
  });
});
