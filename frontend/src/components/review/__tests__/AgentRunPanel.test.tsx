import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  activeRun: { value: undefined as unknown },
  createMutate: vi.fn(),
  cancelMutate: vi.fn(),
  replyMutate: vi.fn(),
}));

vi.mock('@/api/agent', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/agent')>();
  return {
    ...actual,
    useActiveAgentRun: () => mocks.activeRun.value,
    useAgentProviders: () => ({
      data: {
        provider_default: 'openai_api',
        providers: {
          openai_api: {
            configured: true,
            key_last4: 'abcd',
            base_url: '',
            default_model: 'gpt-5',
            reasoning_effort: 'medium',
            available: true,
          },
          chatgpt_sub: {
            configured: false,
            key_last4: null,
            base_url: '',
            default_model: '',
            reasoning_effort: '',
            available: false,
          },
        },
      },
      isLoading: false,
    }),
    useAgentRunEvents: () => [],
    useCreateAgentRun: () => ({ mutate: mocks.createMutate, isPending: false }),
    useCancelAgentRun: () => ({ mutate: mocks.cancelMutate, isPending: false }),
    useReplyAgentCheckpoint: () => ({ mutate: mocks.replyMutate, isPending: false }),
    useUpdateAgentRunConfiguration: () => ({ mutate: vi.fn(), isPending: false }),
  };
});

import { LanguageProvider } from '@/i18n';
import { AgentRunPanel } from '@/components/review/AgentRunPanel';
import { AgentTranscript } from '@/components/review/AgentRunTranscript';
import type { AgentRunEvent } from '@/api/agent';

function renderPanel() {
  return render(
    <MemoryRouter>
      <LanguageProvider>
        <AgentRunPanel submissionId={42} questionName="DTS208" />
      </LanguageProvider>
    </MemoryRouter>,
  );
}

const RUNNING_RUN = {
  id: 7,
  submission_id: 42,
  provider: 'openai_api',
  model: 'gpt-5',
  reasoning_effort: 'medium',
  effective_at: 'current',
  status: 'running',
  checkpoint: null,
  teacher_verdict: null,
  teacher_note: null,
  error_message: null,
  attempts: 1,
  max_attempts: 3,
  usage: null,
  created_at: '2026-10-13T08:00:00',
  finished_at: null,
};

function testResults(isolation?: string) {
  return {
    suite_id: 3,
    version: 1,
    summary: { total: 1, passed: 1, failed: 0 },
    results: [
      {
        id: 'q1-t1',
        question_number: 1,
        title: 'square',
        mode: 'program_io' as const,
        language: 'python' as const,
        status: 'passed' as const,
        output: '标准输出与预期一致',
        ...(isolation ? { isolation } : {}),
      },
    ],
  };
}

describe('AgentRunPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 固定中文 locale:t() 在 zh-CN 下直接返回 key 本身
    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: 'zh-CN',
    });
    window.localStorage.setItem('ai-marking-locale', 'zh-CN');
    mocks.activeRun.value = {
      isLoading: false,
      isError: true,
      error: { response: { status: 404 } },
    };
  });

  it('无活跃 run 时渲染开始批改面板(通道/模型/强度)', () => {
    renderPanel();

    expect(screen.getByText('开始 AI 批改')).toBeInTheDocument();
    expect(screen.getByText('官方 OpenAI API')).toBeInTheDocument();
    expect(screen.getByText('ChatGPT 订阅')).toBeInTheDocument();
    expect(screen.getByText('未登录')).toBeInTheDocument();
    expect(screen.getByLabelText('模型 ID')).toBeInTheDocument();
    expect(screen.getByLabelText('思考强度')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始批改' })).toBeInTheDocument();
  });

  it('已配置通道时显示 key 后四位', () => {
    renderPanel();

    expect(screen.getByText('已配置 ···abcd')).toBeInTheDocument();
  });

  it('运行中展示状态、运行配置与取消按钮', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: { run: RUNNING_RUN, latest_seq: 1, applied_config: null, pending_config: null },
    };
    renderPanel();

    expect(screen.getByText('批改进行中')).toBeInTheDocument();
    expect(screen.getByText('运行配置')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消批改' })).toBeInTheDocument();
  });

  it('隔离降级时在自动测试结果旁给出提示', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: {
        run: { ...RUNNING_RUN, code_test_results: testResults('seatbelt:partial') },
        latest_seq: 1,
        applied_config: null,
        pending_config: null,
      },
    };
    renderPanel();

    expect(screen.getByText('自动测试结果 · v1')).toBeInTheDocument();
    expect(screen.getByText('沙箱隔离（受限）')).toBeInTheDocument();
  });

  it('完整隔离或旧 run 缺少字段时不显示隔离提示', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: {
        run: { ...RUNNING_RUN, code_test_results: testResults('container:full') },
        latest_seq: 1,
        applied_config: null,
        pending_config: null,
      },
    };
    renderPanel();

    expect(screen.queryByText('容器隔离（完整）')).not.toBeInTheDocument();
  });

  it('订阅 run 的思考强度只提供实测档位', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: {
        run: {
          ...RUNNING_RUN,
          provider: 'chatgpt_sub',
          model: 'gpt-6.1-sol',
          reasoning_effort: 'high',
        },
        latest_seq: 1,
        applied_config: null,
        pending_config: null,
      },
    };
    renderPanel();

    const select = screen.getByLabelText('思考强度');
    const values = Array.from(select.querySelectorAll('option')).map(
      (option) => (option as HTMLOptionElement).value,
    );

    expect(values).toEqual(['', 'low', 'medium', 'high', 'xhigh', 'max']);
    expect(values).not.toContain('minimal');
  });

  it('官方 run 保留 minimal 档位', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: { run: RUNNING_RUN, latest_seq: 1, applied_config: null, pending_config: null },
    };
    renderPanel();

    const select = screen.getByLabelText('思考强度');
    const values = Array.from(select.querySelectorAll('option')).map(
      (option) => (option as HTMLOptionElement).value,
    );

    expect(values).toContain('minimal');
    expect(values).not.toContain('xhigh');
  });

  it('等待教师确认时展示检查点表单(含义务文案)', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: {
        run: {
          ...RUNNING_RUN,
          status: 'waiting_for_teacher',
          checkpoint: {
            type: 'code_consistency',
            message: '请核对代码运行结果与报告的差异。',
            asked_at: '2026-10-13T08:01:00',
            expires_at: null,
          },
        },
        latest_seq: 2,
        applied_config: null,
        pending_config: null,
      },
    };
    renderPanel();

    expect(screen.getByText('教师检查点')).toBeInTheDocument();
    expect(screen.getByText('请核对代码运行结果与报告的差异。')).toBeInTheDocument();
    expect(
      screen.getByText(
        '可在本地运行学生代码后核对结果再确认，或直接确认；选择"不一致"时必须填写说明。',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认一致,继续' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '不一致,要求修正' })).toBeInTheDocument();
  });

  it('失败终态展示错误与重新发起面板', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: {
        run: {
          ...RUNNING_RUN,
          status: 'failed',
          error_message: 'API key 无效',
          finished_at: '2026-10-13T08:05:00',
        },
        latest_seq: 3,
        applied_config: null,
        pending_config: null,
      },
    };
    renderPanel();

    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('API key 无效')).toBeInTheDocument();
    // 失败后可重新配置并发起
    expect(screen.getByText('开始 AI 批改')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始批改' })).toBeInTheDocument();
  });

  it('已完成终态不再提供重新发起入口', () => {
    mocks.activeRun.value = {
      isLoading: false,
      data: {
        run: { ...RUNNING_RUN, status: 'completed', finished_at: '2026-10-13T08:06:00' },
        latest_seq: 4,
        applied_config: null,
        pending_config: null,
      },
    };
    renderPanel();

    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始批改' })).not.toBeInTheDocument();
  });
});

function agentEvent(
  seq: number,
  agent: string,
  kind: string,
  payload: Record<string, unknown> = {},
): AgentRunEvent {
  return {
    seq,
    kind,
    payload: { agent, ...payload },
    created_at: '2026-10-13T08:00:00',
  };
}

function renderTranscript(events: AgentRunEvent[]) {
  return render(
    <LanguageProvider>
      <AgentTranscript events={events} />
    </LanguageProvider>,
  );
}

describe('AgentTranscript', () => {
  it('按产出 agent 分组并显示徽标', () => {
    const { container } = renderTranscript([
      agentEvent(1, 'supervisor', 'message_delta', { text: '开始读取评分包' }),
      agentEvent(2, 'item:r1', 'message_delta', { text: '第 1 项证据不足' }),
      agentEvent(3, 'supervisor', 'tool_finished', { title: '已保存评分建议' }),
    ]);

    const badges = screen.getAllByTestId('agent-badge');
    expect(badges.map((badge) => badge.textContent)).toEqual(['主管', '评分项 r1']);
    const text = container.textContent ?? '';
    expect(text).toContain('开始读取评分包');
    // 同一 agent 的后续事件并入同一分组
    expect(text).toContain('已保存评分建议');
    expect(text).toContain('第 1 项证据不足');
  });

  it('超过 300 条事件时只渲染最近 300 条', () => {
    const events = Array.from({ length: 305 }, (_, index) =>
      agentEvent(index + 1, 'supervisor', 'message_delta', {
        text: `内容-${String(index + 1).padStart(3, '0')}`,
      }),
    );
    const { container } = renderTranscript(events);

    const text = container.textContent ?? '';
    expect(text).not.toContain('内容-001');
    expect(text).not.toContain('内容-005');
    expect(text).toContain('内容-006');
    expect(text).toContain('内容-305');
    expect(text).toContain('仅显示最近 300 条事件');
  });

  it('无文本事件不渲染转录面板', () => {
    const { container } = renderTranscript([
      agentEvent(1, 'supervisor', 'turn_completed', { stop_reason: 'stop' }),
    ]);

    expect(container.textContent).toBe('');
    expect(screen.queryByText('执行转录')).not.toBeInTheDocument();
  });
});
