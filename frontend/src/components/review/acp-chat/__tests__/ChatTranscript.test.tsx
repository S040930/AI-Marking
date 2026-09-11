import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ChatTranscript } from '@/components/review/acp-chat/ChatTranscript';
import type { AcpChatEvent } from '@/api/acpChat';

function makeEvent(seq: number, kind: string, payload: Record<string, unknown>): AcpChatEvent {
  return { seq, kind, payload, created_at: '2026-09-08T10:00:00' };
}

describe('ChatTranscript', () => {
  it('用户消息渲染为全宽边框卡,助手回复平铺', () => {
    render(
      <ChatTranscript
        events={[
          makeEvent(1, 'user_message', { text: '总结批改意见' }),
          makeEvent(2, 'agent_message_chunk', { text: '报告结构完整,' }),
          makeEvent(3, 'message_delta', { text: '建议补充测试。' }),
        ]}
      />,
    );

    expect(screen.getByText('总结批改意见')).toBeInTheDocument();
    // 助手文本经 markdown 渲染,增量合入同一 <p>
    const paragraphs = Array.from(document.querySelectorAll('p'));
    expect(
      paragraphs.some((el) => el.textContent === '报告结构完整,建议补充测试。'),
    ).toBe(true);
  });

  it('助手消息中的 markdown 代码块与行内代码按排版渲染,不裸露围栏符号', () => {
    render(
      <ChatTranscript
        events={[
          makeEvent(1, 'agent_message_chunk', {
            text: '缺少 `netflix.csv`:\n\n```text\nFileNotFoundError\n```\n',
          }),
        ]}
      />,
    );

    expect(screen.getByText('FileNotFoundError')).toBeInTheDocument();
    expect(document.querySelector('pre code')).not.toBeNull();
    // 围栏符号 ``` 不应出现在渲染结果里
    expect(screen.queryByText(/```/)).not.toBeInTheDocument();
  });

  it('无 tool_call_id 时按独立行渲染', () => {
    render(
      <ChatTranscript
        events={[
          makeEvent(1, 'tool_started', { title: '工具 A' }),
          makeEvent(2, 'tool_started', { title: '工具 B' }),
        ]}
      />,
    );

    expect(screen.getByText('工具 A')).toBeInTheDocument();
    expect(screen.getByText('工具 B')).toBeInTheDocument();
  });

  it('tool_finished 按 tool_call_id 合并进单卡,展开后可见输出', () => {
    render(
      <ChatTranscript
        events={[
          makeEvent(1, 'tool_started', {
            tool_call_id: 't1',
            title: '读取报告 PDF',
            kind: 'execute',
          }),
          makeEvent(2, 'tool_finished', {
            tool_call_id: 't1',
            title: '读取报告 PDF',
            kind: 'execute',
            status: 'completed',
            output: 'PDF 共 12 页',
          }),
        ]}
      />,
    );

    // 只有一张终端卡,而不是两行;已完成卡默认收起,点击展开后可见输出
    expect(screen.getAllByText('读取报告 PDF')).toHaveLength(1);
    fireEvent.click(screen.getByText('读取报告 PDF'));
    expect(screen.getByText('PDF 共 12 页')).toBeInTheDocument();
  });

  it('终端卡默认对命令类工具展示 $ 提示符,非命令类不展示', () => {
    render(
      <ChatTranscript
        events={[
          makeEvent(1, 'tool_started', { title: 'pytest -q', kind: 'execute' }),
          makeEvent(2, 'tool_started', { title: '读取评分标准' }),
        ]}
      />,
    );

    expect(screen.getByText('$')).toBeInTheDocument();
  });

  it('终端卡可点击展开与收起', () => {
    render(
      <ChatTranscript
        events={[
          makeEvent(1, 'tool_started', { tool_call_id: 't2', title: 'ls -la' }),
          makeEvent(2, 'tool_finished', {
            tool_call_id: 't2',
            title: 'ls -la',
            status: 'completed',
            output: 'file-a.pdf',
          }),
        ]}
      />,
    );

    // 合并为单卡,已完成态默认收起,点击展开后可见输出,再点收起隐藏
    expect(screen.getAllByText('ls -la')).toHaveLength(1);
    fireEvent.click(screen.getByText('ls -la'));
    expect(screen.getByText('file-a.pdf')).toBeInTheDocument();
    fireEvent.click(screen.getByText('ls -la'));
    expect(screen.queryByText('file-a.pdf')).not.toBeInTheDocument();
  });

  it('error 事件渲染为红色横幅', () => {
    render(<ChatTranscript events={[makeEvent(1, 'error', { text: '助手进程退出' })]} />);

    expect(screen.getByText('助手进程退出')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

