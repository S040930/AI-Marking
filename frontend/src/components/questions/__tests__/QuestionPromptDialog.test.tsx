import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { LanguageProvider } from '@/i18n';

const mocks = vi.hoisted(() => ({
  gradingPrompt: {
    data: undefined as unknown,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  },
}));

vi.mock('@/api/questions', () => ({
  useGradingPrompt: () => mocks.gradingPrompt,
}));

import { QuestionPromptDialog } from '@/components/questions/QuestionPromptDialog';

const QUESTION = {
  id: 7,
  config_profile_id: 1,
  name: '期末作文',
  original_filename: 'essay.pdf',
  status: 'ready' as const,
  error_message: null,
  replacement_status: null,
  replacement_error_message: null,
  created_at: '2026-07-03T10:00:00',
  updated_at: '2026-07-03T10:00:00',
  last_used_at: null,
  submission_count: 3,
};

function renderDialog() {
  return render(
    <LanguageProvider>
      <QuestionPromptDialog
        question={QUESTION}
        onOpenChange={() => {}}
      />
    </LanguageProvider>,
  );
}

beforeEach(() => {
  Object.defineProperty(window.navigator, 'language', {
    configurable: true,
    value: 'zh-CN',
  });
  mocks.gradingPrompt.data = undefined;
  mocks.gradingPrompt.isLoading = false;
  mocks.gradingPrompt.isError = false;
  mocks.gradingPrompt.refetch.mockReset();
  mocks.gradingPrompt.refetch.mockResolvedValue(undefined);
});

describe('QuestionPromptDialog', () => {
  it('展示加载骨架', () => {
    mocks.gradingPrompt.isLoading = true;
    renderDialog();
    expect(screen.getByText('批改提示词')).toBeInTheDocument();
    expect(document.querySelector('[data-slot="skeleton"]')).toBeInTheDocument();
  });

  it('展示精简提示词（题目名 + 引用 ai-marking-grader skill）', () => {
    mocks.gradingPrompt.data = {
      question_id: 7,
      name: '期末作文',
      grading_mode: 'external_agent',
      review_enabled: true,
      source: 'configured',
      snapshot_id: 'snap-1',
      total_max_score: 100,
      needs_rubric: false,
      ocr_text: 'Task 1: 100 points',
      grading_policy: { resolved_rubric: { text: 'Task 1 (100分)' } },
      text: '--- question ---\nTask 1: 100 points\n--- grading_policy ---\n{"resolved_rubric": {"text": "Task 1 (100分)"}}',
    };
    renderDialog();
    expect(screen.getByText('批改提示词')).toBeInTheDocument();
    // 提示词只含题目标识与对 ai-marking-grader skill 的引用，不内嵌 OCR / 评分标准全文
    expect(screen.getByText(/期末作文/)).toBeInTheDocument();
    expect(screen.getByText(/ai-marking-grader/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制' })).toBeInTheDocument();
    expect(screen.queryByText(/--- question ---/)).not.toBeInTheDocument();
  });

  it('生成失败时展示错误与重试按钮', () => {
    mocks.gradingPrompt.isError = true;
    renderDialog();
    expect(screen.getByText('提示词生成失败')).toBeInTheDocument();
    const retry = screen.getByRole('button', { name: '重试' });
    fireEvent.click(retry);
    expect(mocks.gradingPrompt.refetch).toHaveBeenCalledTimes(1);
  });
});
