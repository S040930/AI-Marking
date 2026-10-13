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
    expect(screen.getByText('未开放')).toBeInTheDocument();
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
