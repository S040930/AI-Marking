import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { McpWaitingPanel } from '@/components/review/McpWaitingPanel';

describe('McpWaitingPanel', () => {
  it('生成含 workflow_mode、submission_id 与题目名的详细恢复提示词', () => {
    render(
      <McpWaitingPanel
        submissionId={33}
        questionName="DTS208TC_CW2_Paper"
        questionId="dts208tc-cw2-paper"
      />,
    );

    expect(screen.getByText('等待编程助手评分')).toBeInTheDocument();
    expect(screen.getByText(/workflow_mode: mcp_existing/)).toBeInTheDocument();
    expect(screen.getByText(/submission_id: 33/)).toBeInTheDocument();
    expect(
      screen.getByText(/题目：DTS208TC_CW2_Paper（question_id: dts208tc-cw2-paper）/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/请继续使用 AI-Marking 批改作业。/),
    ).toBeInTheDocument();
  });

  it('题目名或 question_id 缺失时不渲染,也不出现作业编号', () => {
    const { container } = render(
      <McpWaitingPanel submissionId={33} questionName={null} questionId={null} />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/#\d+/)).not.toBeInTheDocument();
  });

  it('card 变体渲染独立外壳,内容一致', () => {
    render(
      <McpWaitingPanel
        submissionId={33}
        questionName="Q7"
        questionId="q7"
        variant="card"
      />,
    );

    expect(screen.getByText('等待编程助手评分')).toBeInTheDocument();
    expect(screen.getByText(/workflow_mode: mcp_existing/)).toBeInTheDocument();
    expect(screen.getByText(/题目：Q7（question_id: q7）/)).toBeInTheDocument();
  });
});
