import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CodexWaitingPanel } from '@/pages/ReviewPage';

describe('CodexWaitingPanel', () => {
  it('展示用于中断任务恢复的作业 ID 和继续指令', () => {
    render(<CodexWaitingPanel submissionId={42} />);

    expect(screen.getByText('Codex 作业 #42')).toBeInTheDocument();
    expect(screen.getByText('等待 Codex 继续评分')).toBeInTheDocument();
    expect(
      screen.getByText('请继续使用 AI-Marking 批改作业 #42。'),
    ).toBeInTheDocument();
  });
});
