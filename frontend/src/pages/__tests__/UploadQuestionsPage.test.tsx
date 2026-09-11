import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LanguageProvider } from '@/i18n';

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  retryMutate: vi.fn(),
  // 模拟服务端各题目的 OCR 状态（queryKey 维度）
  serverStatus: new Map<string, { status: string; error_message?: string | null }>(),
  listeners: new Set<(id: string) => void>(),
}));

vi.mock('@/api/questions', async () => {
  const React = await import('react');
  return {
    useCreateQuestion: () => ({ mutate: mocks.mutate, isPending: false }),
    useRetryQuestionOcr: () => ({
      mutate: mocks.retryMutate,
      isPending: false,
    }),
    useQuestionWatch: (id: string | null) => {
      // 换 queryKey（换题目）时重新读取"服务端"状态，模拟无缓存的新查询
      const [data, setData] = React.useState(
        id ? (mocks.serverStatus.get(id) ?? null) : null,
      );
      const lastId = React.useRef(id);
      if (lastId.current !== id) {
        lastId.current = id;
        setData(id ? (mocks.serverStatus.get(id) ?? null) : null);
      }
      React.useEffect(() => {
        if (id === null) return;
        const listener = (changedId: string) => {
          if (changedId !== id) return;
          setData(mocks.serverStatus.get(changedId) ?? null);
        };
        mocks.listeners.add(listener);
        return () => {
          mocks.listeners.delete(listener);
        };
      }, [id]);
      return { data };
    },
  };
});

// 模拟某题目的 OCR 状态变化（触发页面重渲染）
function setOcrStatus(
  id: string,
  status: 'ready' | 'failed',
  error_message?: string,
) {
  mocks.serverStatus.set(id, { status, error_message });
  mocks.listeners.forEach((listener) => listener(id));
}

import UploadQuestionsPage from '@/pages/UploadQuestionsPage';

function makeTree() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <MemoryRouter initialEntries={['/questions/upload']}>
          <Routes>
            <Route path="/questions/upload" element={<UploadQuestionsPage />} />
            <Route path="/submissions/upload" element={<div>上传作业页面</div>} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>
    </QueryClientProvider>
  );
}

function renderPage() {
  return render(makeTree());
}

function selectFiles(names: string[]) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const files = names.map(
    (name) => new File(['pdf'], name, { type: 'application/pdf' }),
  );
  fireEvent.change(input, { target: { files } });
}

describe('UploadQuestionsPage', () => {
  beforeEach(() => {
    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: 'zh-CN',
    });
    window.sessionStorage.clear();
    mocks.mutate.mockReset();
    mocks.retryMutate.mockReset();
    mocks.serverStatus.clear();
    mocks.listeners.clear();
    mocks.mutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: 'DTS208TC_CW1_Paper',
        name: _vars.name,
        status: 'pending',
      });
    });
  });

  it('展示上传页标题与上传按钮', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: '上传题目' })).toBeInTheDocument();
    const uploadButton = screen.getByRole('button', { name: '上传' });
    expect(uploadButton).toBeDisabled();
  });

  it('选择 PDF 后显示文件行并启用上传按钮', () => {
    renderPage();
    selectFiles(['essay.pdf']);
    const nameInput = screen.getByLabelText('题目名称 1');
    expect(nameInput).toHaveValue('essay');
    expect(screen.getByRole('button', { name: '上传' })).toBeEnabled();
  });

  it('上传成功后轮询 OCR，ready 后跳转上传作业页并写入预选标记', async () => {
    renderPage();
    selectFiles(['essay.pdf']);
    fireEvent.click(screen.getByRole('button', { name: '上传' }));
    expect(mocks.mutate).toHaveBeenCalledWith(
      { file: expect.any(File), name: 'essay' },
      expect.any(Object),
    );
    expect(screen.getByText('识别中')).toBeInTheDocument();

    setOcrStatus('DTS208TC_CW1_Paper', 'ready');
    await waitFor(() =>
      expect(screen.getByText('上传作业页面')).toBeInTheDocument(),
    );
    const marker = JSON.parse(
      window.sessionStorage.getItem('ocr-ready-questions') ?? '{}',
    );
    expect(marker.names).toEqual(['essay']);
  });

  it('OCR 失败时留在本页显示错误与重试按钮', async () => {
    renderPage();
    selectFiles(['essay.pdf']);
    fireEvent.click(screen.getByRole('button', { name: '上传' }));

    setOcrStatus('DTS208TC_CW1_Paper', 'failed', '识别服务异常');
    await waitFor(() =>
      expect(screen.getByText('识别服务异常')).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.queryByText('上传作业页面')).not.toBeInTheDocument();
  });

  it('多文件时全部识别完成才跳转', async () => {
    renderPage();
    selectFiles(['a.pdf', 'b.pdf']);
    fireEvent.click(screen.getByRole('button', { name: '上传' }));
    expect(screen.getAllByText('识别中')).toHaveLength(2);

    // 第一个 ready → 仍在等待第二个
    setOcrStatus('DTS208TC_CW1_Paper', 'ready');
    await waitFor(() => expect(screen.getByText('识别完成')).toBeInTheDocument());
    expect(screen.queryByText('上传作业页面')).not.toBeInTheDocument();

    // 第二个也 ready → 跳转
    setOcrStatus('DTS208TC_CW1_Paper', 'ready');
    await waitFor(() =>
      expect(screen.getByText('上传作业页面')).toBeInTheDocument(),
    );
    const marker = JSON.parse(
      window.sessionStorage.getItem('ocr-ready-questions') ?? '{}',
    );
    expect(marker.names).toEqual(['a', 'b']);
  });

  it('重试成功后同样跳转上传作业页', async () => {
    renderPage();
    selectFiles(['essay.pdf']);
    fireEvent.click(screen.getByRole('button', { name: '上传' }));

    setOcrStatus('DTS208TC_CW1_Paper', 'failed', '识别失败');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument(),
    );

    // OCR 失败的重试必须走 retry-ocr(复用同一题目),而非重复 create
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(mocks.retryMutate).toHaveBeenCalledWith(
      { id: 'DTS208TC_CW1_Paper', file: expect.any(File) },
      expect.any(Object),
    );
    expect(mocks.mutate).toHaveBeenCalledTimes(1);

    // 重试时后端把题目状态重置回 pending，重新轮询
    mocks.serverStatus.set('DTS208TC_CW1_Paper', { status: 'pending' });
    mocks.retryMutate.mock.calls[0][1]?.onSuccess?.({
      id: 'DTS208TC_CW1_Paper',
      name: 'essay',
      status: 'pending',
    });
    await waitFor(() => expect(screen.getByText('识别中')).toBeInTheDocument());

    setOcrStatus('DTS208TC_CW1_Paper', 'ready');
    await waitFor(() =>
      expect(screen.getByText('上传作业页面')).toBeInTheDocument(),
    );
  });

  it('空名称会提示错误', () => {
    renderPage();
    selectFiles(['essay.pdf']);
    const nameInput = screen.getByLabelText('题目名称 1');
    fireEvent.change(nameInput, { target: { value: '  ' } });
    fireEvent.click(screen.getByRole('button', { name: '上传' }));
    expect(screen.getByText('题目名称不能为空')).toBeInTheDocument();
  });
});
