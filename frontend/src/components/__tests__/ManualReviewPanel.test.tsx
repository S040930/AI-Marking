import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ManualReviewPanel from '@/components/ManualReviewPanel';
import { normalizeSuggestion } from '@/components/review/normalizeSuggestion';
import type { AiSuggestion, SubmissionDetail } from '@/api/submissions';

const suggestion: AiSuggestion = {
  score: 70, max_score: 100, confidence: 0.8, feedback: 'Original feedback',
  details: [{ rubric_item_id: 'rubric_task', criterion: 'Task', max_score: 100,
    score: 70, comment: 'Complete', evidence: ['Evidence'] }],
};

function props() {
  return { suggestion, gradingRevision: 1, isReadOnly: false, reviewerName: 'Teacher',
    onFinalize: vi.fn(), isFinalizing: false, onReloadSuggestion: vi.fn() };
}

const submit = () => screen.getByRole('button', { name: '复核并提交最终评分' });

describe('ManualReviewPanel version-bound drafts', () => {
  it('保留人工草稿和原满分，载入最新建议后才可确认新版本', () => {
    const initial = props();
    const { rerender } = render(<ManualReviewPanel {...initial} />);
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '85' } });
    fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Teacher draft' } });
    const updated = { ...suggestion, score: 40, max_score: 50,
      details: [{ ...suggestion.details[0], rubric_item_id: 'rubric_new_task', score: 40, max_score: 50 }] };
    rerender(<ManualReviewPanel {...initial} suggestion={updated} gradingRevision={2} />);
    expect(screen.getByRole('spinbutton')).toHaveValue(85);
    expect(screen.getByRole('spinbutton')).toHaveAttribute('max', '100');
    expect(screen.getAllByRole('textbox')[0]).toHaveValue('Teacher draft');
    expect(screen.getByRole('alert')).toHaveTextContent('已保留你的修改');
    expect(submit()).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '载入最新建议' }));
    expect(screen.getByRole('spinbutton')).toHaveValue(40);
    expect(submit()).toBeEnabled();
    fireEvent.click(submit());
    expect(initial.onReloadSuggestion).toHaveBeenCalledOnce();
    expect(initial.onFinalize).toHaveBeenCalledWith(expect.objectContaining({
      expected_revision: 2, max_score: 50,
      details: [expect.objectContaining({ rubric_item_id: 'rubric_new_task', score: 40 })],
    }));
  });

  it('未编辑的表单自动载入新建议', () => {
    const initial = props();
    const { rerender } = render(<ManualReviewPanel {...initial} />);
    rerender(<ManualReviewPanel {...initial} gradingRevision={2}
      suggestion={{ ...suggestion, score: 60, details: [{ ...suggestion.details[0], score: 60 }] }} />);
    expect(screen.getByRole('spinbutton')).toHaveValue(60);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.click(submit());
    expect(initial.onFinalize).toHaveBeenCalledWith(expect.objectContaining({ expected_revision: 2 }));
  });

  it('同内容的新版本仍禁止提交旧草稿', () => {
    const initial = props();
    const { rerender } = render(<ManualReviewPanel {...initial} />);
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '85' } });
    rerender(<ManualReviewPanel {...initial} gradingRevision={2} />);
    expect(screen.getByRole('spinbutton')).toHaveValue(85);
    expect(submit()).toBeDisabled();
  });

  it('同版本独立复核刷新不重置草稿，提交携带原版本及评分项 ID', () => {
    const initial = props();
    const { rerender } = render(<ManualReviewPanel {...initial} />);
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '85' } });
    rerender(<ManualReviewPanel {...initial} suggestion={JSON.parse(JSON.stringify(suggestion))} />);
    expect(screen.getByRole('spinbutton')).toHaveValue(85);
    fireEvent.click(submit());
    expect(initial.onFinalize).toHaveBeenCalledWith(expect.objectContaining({
      expected_revision: 1, details: [expect.objectContaining({ rubric_item_id: 'rubric_task', score: 85 })],
    }));
  });

  it('409 冲突保留草稿；另一窗口确认后禁用编辑和提交', () => {
    const initial = props();
    const { rerender } = render(<ManualReviewPanel {...initial} />);
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '85' } });
    rerender(<ManualReviewPanel {...initial} hasVersionConflict />);
    expect(screen.getByRole('spinbutton')).toHaveValue(85);
    expect(submit()).toBeDisabled();
    rerender(<ManualReviewPanel {...initial} hasVersionConflict isReadOnly />);
    expect(screen.getByRole('spinbutton')).toBeDisabled();
    expect(screen.queryByRole('button', { name: '复核并提交最终评分' })).not.toBeInTheDocument();
  });

  it('规范化建议保留权威评分项 ID', () => {
    const result = normalizeSuggestion({ assessment_suggestion: suggestion } as SubmissionDetail);
    expect(result?.details[0].rubric_item_id).toBe('rubric_task');
  });
});
