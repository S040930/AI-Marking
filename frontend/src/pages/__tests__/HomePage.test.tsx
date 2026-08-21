import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import HomePage from '@/pages/HomePage';
import { LanguageProvider } from '@/i18n';

function renderWithProviders(ui: React.ReactElement) {
  return render(
    <LanguageProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </LanguageProvider>,
  );
}

describe('HomePage', () => {
  beforeEach(() => {
    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: 'zh-CN',
    });
  });

  it('引导页展示批改流程的三个步骤', () => {
    renderWithProviders(<HomePage />);

    expect(screen.getByText('批改流程')).toBeInTheDocument();
    expect(screen.getByText('准备题目')).toBeInTheDocument();
    expect(screen.getByText('编程助手提交作业')).toBeInTheDocument();
    expect(screen.getByText('在网页确认成绩')).toBeInTheDocument();
  });

  it('提供上传题目和查看历史记录的入口', () => {
    renderWithProviders(<HomePage />);

    const uploadLink = screen.getByRole('link', { name: '上传题目' });
    expect(uploadLink).toHaveAttribute('href', '/questions/upload');

    const historyLink = screen.getByRole('link', { name: '查看历史记录' });
    expect(historyLink).toHaveAttribute('href', '/history');
  });
});
