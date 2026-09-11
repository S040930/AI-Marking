import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  createMutate: vi.fn(),
  cancelMutate: vi.fn(),
  replyMutate: vi.fn(),
  readyAgent: {
    agent_id: 'codex-acp',
    display_name: 'Codex',
    whitelist_package: '@agentclientprotocol/codex-acp',
    distributions: ['npx'],
    installed_version: '1.0.0',
    available_version: '1.0.0',
    connection_status: 'ready',
  },
  agents: {
    value: [
      {
        agent_id: 'codex-acp',
        display_name: 'Codex',
        whitelist_package: '@agentclientprotocol/codex-acp',
        distributions: ['npx'],
        installed_version: '1.0.0',
        available_version: '1.0.0',
        connection_status: 'ready',
      },
    ] as unknown[],
  },
  // 由各测试用例注入的 run 详情与事件流
  runDetail: { value: undefined as { run: unknown } | undefined },
  events: { value: [] as unknown[] },
  // 404(error 状态)模拟:取消/终态后 by-submission 无活跃 run
  run404: { value: false as boolean },
  updateRunConfigMutate: vi.fn(),
  catalog: { value: undefined as unknown },
}));

const baseCatalog = {
  agent_id: 'codex-acp',
  agent_version: '1.0.0',
  selected_model_id: 'codex-default',
  models: [{ id: 'codex-default', label: 'Codex 默认', current: true }],
  reasoning_efforts: [{ id: 'medium', label: '中等', current: true }],
  speed_modes: [
    { id: 'standard', label: '标准', available: true, current: true },
    { id: 'fast', label: '快速', available: false, current: false },
  ],
  capability_error: null,
};

vi.mock('@/api/acp', () => ({
  isTerminalRun: (status: string) =>
    ['completed', 'failed', 'cancelled'].includes(status),
  useAcpAgents: () => ({ data: { agents: mocks.agents.value }, isLoading: false }),
  useActiveAcpRun: () => ({
    data:
      mocks.runDetail.value && !mocks.run404.value
        ? { run: mocks.runDetail.value.run, latest_seq: 0 }
        : undefined,
    isLoading: false,
    isError: mocks.run404.value,
    error: mocks.run404.value
      ? { response: { status: 404 } }
      : undefined,
  }),
  useAcpRunEvents: () => mocks.events.value,
  useCancelAcpRun: () => ({ mutate: mocks.cancelMutate, isPending: false }),
  useReplyAcpCheckpoint: () => ({
    mutate: mocks.replyMutate,
    isPending: false,
  }),
  useCodexConfiguration: () => ({
    data: mocks.catalog.value,
    isLoading: false,
    isFetching: false,
  }),
  useUpdateRunCodexConfiguration: () => ({
    mutate: mocks.updateRunConfigMutate,
    isPending: false,
  }),
  selectionToRequest: (selection: {
    modelId: string | null;
    reasoningEffort: string | null;
    speedMode: string;
  }) => ({
    model_id: selection.modelId,
    reasoning_effort: selection.reasoningEffort,
    speed_mode: selection.speedMode,
    fast_confirmed: selection.speedMode === 'fast',
  }),
}));

import { AcpMarkingPanel } from '@/components/review/AcpMarkingPanel';
import { LanguageProvider } from '@/i18n';
import type { AcpRun, AcpRunEvent } from '@/api/acp';

function renderWithProviders(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

function makeRun(overrides: Partial<AcpRun>): AcpRun {
  return {
    id: 7,
    submission_id: 42,
    agent_id: 'codex-acp',
    permission_mode: 'ask',
    codex_config: {
      model_id: 'codex-default',
      reasoning_effort: 'medium',
      speed_mode: 'standard',
    },
    applied_codex_config: null,
    pending_codex_config: null,
    effective_at: 'current',
    status: 'running',
    acp_session_id: null,
    checkpoint: null,
    teacher_verdict: null,
    teacher_note: null,
    error_message: null,
    attempts: 1,
    max_attempts: 3,
    created_at: '2026-09-07T10:00:00',
    finished_at: null,
    ...overrides,
  };
}

describe('AcpMarkingPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem('ai-marking-locale', 'zh-CN');
    mocks.runDetail.value = undefined;
    mocks.events.value = [];
    mocks.run404.value = false;
    mocks.catalog.value = baseCatalog;
    mocks.agents.value = [{ ...mocks.readyAgent }];
  });

  it('无 run 时展示启动入口并交给父级打开助手,保留 MCP 恢复兜底', () => {
    const onStartInAssistant = vi.fn();
    renderWithProviders(
      <AcpMarkingPanel
        submissionId={42}
        questionName="DTS208TC_CW1_Paper"
        questionId="dts208tc-cw1-paper"
        onStartInAssistant={onStartInAssistant}
      />,
    );

    // 用题目名指代作业,不再出现「作业 #N」
    expect(screen.getByText('DTS208TC_CW1_Paper')).toBeInTheDocument();
    expect(screen.queryByText('作业 #42')).not.toBeInTheDocument();
    expect(screen.getByText('启动 Codex 自动批改')).toBeInTheDocument();
    // 模型/权限档位都在助手面板,启动面板不再渲染
    expect(screen.queryByLabelText('权限档位')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Codex 模型')).not.toBeInTheDocument();

    // 启动不再直接发起 run,而是把教师送到右侧 AI 助手
    fireEvent.click(screen.getByRole('button', { name: '开始批改' }));
    expect(onStartInAssistant).toHaveBeenCalledTimes(1);

    // 手动兜底(McpWaitingPanel)仍然可见
    expect(screen.getByText('复制编程助手指令')).toBeInTheDocument();
  });

  it('本机未连接 Codex 时提示先安装,不提供启动入口', () => {
    mocks.agents.value = [];

    renderWithProviders(<AcpMarkingPanel submissionId={42} />);

    expect(screen.getByText('请先在系统设置安装并连接测试 Codex ACP。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始批改' })).not.toBeInTheDocument();
  });

  it('run 取消后 by-submission 404:不沿用 stale 数据,回到启动面板', () => {
    // 模拟取消完成后 query 缓存仍持有旧 run(cancelling),但接口已 404
    mocks.runDetail.value = { run: makeRun({ status: 'cancelling' }) };
    mocks.run404.value = true;

    renderWithProviders(<AcpMarkingPanel submissionId={42} />);

    // 不得显示旧状态"cancelling",应回到可重新发起的启动面板
    expect(screen.queryByText('取消中')).not.toBeInTheDocument();
    expect(screen.getByText('启动 Codex 自动批改')).toBeInTheDocument();
  });

  it('运行中展示状态、取消按钮与事件转录', () => {
    mocks.runDetail.value = { run: makeRun({ status: 'running' }) };
    mocks.events.value = [
      {
        seq: 1,
        kind: 'notice',
        payload: { text: '已创建 ACP 批改运行 (agent: codex-acp)' },
        created_at: '2026-09-07T10:00:00',
      },
      {
        seq: 2,
        kind: 'message_delta',
        payload: { text: '正在阅读作业…' },
        created_at: '2026-09-07T10:00:05',
      },
    ] as AcpRunEvent[];

    renderWithProviders(<AcpMarkingPanel submissionId={42} />);

    expect(screen.getByText('批改进行中')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消批改' })).toBeInTheDocument();
    expect(screen.getByText('执行转录')).toBeInTheDocument();
    expect(screen.getByText('正在阅读作业…')).toBeInTheDocument();
  });

  it('运行中可热切换模型与思考强度,走 PATCH 运行配置接口', () => {
    mocks.runDetail.value = { run: makeRun({ status: 'running' }) };
    mocks.catalog.value = {
      ...baseCatalog,
      models: [
        { id: 'codex-default', label: 'Codex 默认', current: true },
        { id: 'gpt-5', label: 'GPT-5', current: false },
      ],
    };

    renderWithProviders(<AcpMarkingPanel submissionId={42} />);

    fireEvent.click(screen.getByLabelText('模型与思考强度'));
    fireEvent.click(screen.getByRole('option', { name: 'GPT-5' }));

    expect(mocks.updateRunConfigMutate).toHaveBeenCalledWith(
      {
        model_id: 'gpt-5',
        reasoning_effort: 'medium',
        speed_mode: 'standard',
        fast_confirmed: false,
      },
      expect.anything(),
    );
  });

  it('等待教师时展示检查点表单并提交答复', () => {
    mocks.runDetail.value = {
      run: makeRun({
        status: 'waiting_for_teacher',
        checkpoint: {
          type: 'code_consistency',
          message: '代码运行结果与报告一致吗?',
          asked_at: null,
          expires_at: null,
        },
      }),
    };

    renderWithProviders(<AcpMarkingPanel submissionId={42} />);

    expect(screen.getByText('教师检查点')).toBeInTheDocument();
    expect(
      screen.getByText('代码运行结果与报告一致吗?'),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '确认一致,继续' }),
    );
    expect(mocks.replyMutate).toHaveBeenCalledWith(
      { verdict: 'consistent', note: undefined },
      expect.anything(),
    );

    fireEvent.click(
      screen.getByRole('button', { name: '不一致,要求修正' }),
    );
    expect(mocks.replyMutate).toHaveBeenLastCalledWith(
      { verdict: 'mismatch', note: undefined },
      expect.anything(),
    );
  });

  it('终态展示收尾提示', () => {
    mocks.runDetail.value = { run: makeRun({ status: 'completed' }) };

    renderWithProviders(<AcpMarkingPanel submissionId={42} />);

    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(
      screen.getByText('评分建议已保存,页面将自动进入复核。'),
    ).toBeInTheDocument();
  });
});
