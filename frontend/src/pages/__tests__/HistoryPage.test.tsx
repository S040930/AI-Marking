import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge, STATUS_TEXT } from '@/pages/HistoryPage';
import type { SubmissionStatus } from '@/api/submissions';

describe('StatusBadge', () => {
  it('reviewed 状态显示"已审阅"', () => {
    render(<StatusBadge status="reviewed" />);
    expect(screen.getByText(STATUS_TEXT.reviewed)).toBeInTheDocument();
  });

  it('failed 状态显示"失败"', () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByText(STATUS_TEXT.failed)).toBeInTheDocument();
  });

  const processingStatuses: SubmissionStatus[] = [
    'pending',
    'ocr_processing',
    'ocr_done',
    'agent_grading',
    'agent_reviewing',
    'agent_revising',
  ];

  processingStatuses.forEach((status) => {
    it(`${status} 状态显示对应中文文案`, () => {
      render(<StatusBadge status={status} />);
      expect(screen.getByText(STATUS_TEXT[status])).toBeInTheDocument();
    });
  });

  it('reviewed 状态徽章包含 success 相关样式', () => {
    const { container } = render(<StatusBadge status="reviewed" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain('text-success');
  });

  it('failed 状态徽章包含 destructive 样式', () => {
    const { container } = render(<StatusBadge status="failed" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain('bg-destructive');
  });

  it('ready_for_review 状态徽章包含 amber 样式', () => {
    const { container } = render(<StatusBadge status="ready_for_review" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain('text-amber');
  });

  it('processing 状态徽章包含 animate-ping 脉冲动画', () => {
    const { container } = render(<StatusBadge status="ocr_processing" />);
    expect(container.querySelector('.animate-ping')).not.toBeNull();
  });
});

describe('STATUS_TEXT 完整性', () => {
  const allStatuses: SubmissionStatus[] = [
    'pending',
    'ocr_processing',
    'ocr_done',
    'agent_grading',
    'agent_reviewing',
    'agent_revising',
    'ready_for_review',
    'reviewed',
    'failed',
  ];

  allStatuses.forEach((status) => {
    it(`${status} 在 STATUS_TEXT 中有对应文案`, () => {
      expect(STATUS_TEXT[status]).toBeTruthy();
      expect(typeof STATUS_TEXT[status]).toBe('string');
    });
  });
});
