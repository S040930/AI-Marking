import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { McpWaitingPanel } from '@/components/review/McpWaitingPanel';

describe('McpWaitingPanel', () => {
  it('有题目名时生成含题目名的详细恢复提示词', () => {
    render(
      <McpWaitingPanel
        questionName="DTS208TC_CW2_Paper"
        questionId="dts208tc-cw2-paper"
      />,
    );

    expect(screen.getByText('等待编程助手评分')).toBeInTheDocument();
    expect(
      screen.getByText(/题目：DTS208TC_CW2_Paper（question_id: dts208tc-cw2-paper）/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/请继续使用 AI-Marking 批改作业。/),
    ).toBeInTheDocument();
  });

  it('题目名或 question_id 缺失时不渲染,也不出现作业编号', () => {
    const { container } = render(<McpWaitingPanel questionName={null} questionId={null} />);

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/#\d+/)).not.toBeInTheDocument();
  });

  it('card 变体渲染独立外壳,内容一致', () => {
    render(
      <McpWaitingPanel
        questionName="Q7"
        questionId="q7"
        variant="card"
      />,
    );

    expect(screen.getByText('等待编程助手评分')).toBeInTheDocument();
    expect(screen.getByText(/题目：Q7（question_id: q7）/)).toBeInTheDocument();
  });
});
