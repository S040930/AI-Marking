import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { McpWaitingPanel } from '@/components/review/McpWaitingPanel';

describe('McpWaitingPanel', () => {
  it('展示用于中断任务恢复的作业 ID 和继续指令', () => {
    render(<McpWaitingPanel submissionId={42} />);

    expect(screen.getByText('作业 #42 · 等待 MCP 评分')).toBeInTheDocument();
    expect(screen.getByText('等待编程助手评分')).toBeInTheDocument();
    expect(
      screen.getByText('请继续使用 AI-Marking 批改作业 #42。'),
    ).toBeInTheDocument();
  });
});
