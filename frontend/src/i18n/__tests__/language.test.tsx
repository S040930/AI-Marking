import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MainLayout from '@/layouts/MainLayout';
import { LanguageProvider, STORAGE_KEY, useLanguage } from '@/i18n';

function Probe() {
  const { locale, t, setLocale } = useLanguage();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span>{t('题目库')}</span>
      <button type="button" onClick={() => setLocale('en-US')}>
        English
      </button>
    </div>
  );
}

describe('LanguageProvider', () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: () => ({
        matches: false,
        media: '',
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }),
    });
    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: 'zh-CN',
    });
  });

  it('uses the browser locale when no saved preference exists', () => {
    render(
      <LanguageProvider>
        <Probe />
      </LanguageProvider>,
    );

    expect(screen.getByTestId('locale')).toHaveTextContent('zh-CN');
    expect(screen.getAllByText('题目库').length).toBeGreaterThan(0);
  });

  it('prefers a saved locale and persists changes', () => {
    window.localStorage.setItem(STORAGE_KEY, 'en-US');
    render(
      <LanguageProvider>
        <Probe />
      </LanguageProvider>,
    );

    expect(screen.getByTestId('locale')).toHaveTextContent('en-US');
    expect(screen.getAllByText('Question Library').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'English' }));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('en-US');
  });

  it('switches the global navigation labels', () => {
    render(
      <LanguageProvider>
        <MemoryRouter>
          <MainLayout />
        </MemoryRouter>
      </LanguageProvider>,
    );

    expect(screen.getAllByText('题目库').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '切换到英文' }));
    expect(screen.getAllByText('Question Library').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Switch to Chinese' })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('en-US');
  });

  it('renders the upload-question navigation item', () => {
    render(
      <LanguageProvider>
        <MemoryRouter>
          <MainLayout />
        </MemoryRouter>
      </LanguageProvider>,
    );

    const uploadLink = screen.getByRole('link', { name: '上传题目' });
    expect(uploadLink).toHaveAttribute('href', '/questions/upload');
    fireEvent.click(screen.getByRole('button', { name: '切换到英文' }));
    expect(screen.getByRole('link', { name: 'Upload question' })).toHaveAttribute(
      'href',
      '/questions/upload',
    );
  });

  it('侧边栏底部提供收起/展开按钮并可切换', () => {
    render(
      <LanguageProvider>
        <MemoryRouter>
          <MainLayout />
        </MemoryRouter>
      </LanguageProvider>,
    );

    const collapseButton = screen.getByRole('button', { name: '收起侧边栏' });
    expect(collapseButton).toBeInTheDocument();

    fireEvent.click(collapseButton);
    expect(screen.getByRole('button', { name: '展开侧边栏' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '展开侧边栏' }));
    expect(screen.getByRole('button', { name: '收起侧边栏' })).toBeInTheDocument();
  });
});
