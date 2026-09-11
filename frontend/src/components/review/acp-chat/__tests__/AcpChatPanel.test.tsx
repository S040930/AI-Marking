import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  createMutate: vi.fn(),
  sendMutate: vi.fn(),
  permissionMutate: vi.fn(),
  cancelMutate: vi.fn(),
  closeMutate: vi.fn(),
  deleteMutate: vi.fn(),
  updateConfigMutate: vi.fn(),
  // 批改运行(历史 run 的展示与冻结)
  cancelRunMutate: vi.fn(),
  replyCheckpointMutate: vi.fn(),
  // 由各测试用例注入的会话与事件流
  sessions: { value: [] as unknown[] },
  activeChat: { value: undefined as unknown },
  // useAcpChatSession 收到的 chatId:断言"删除后自动选中下一会话"
  sessionQueryId: { value: undefined as number | undefined },
  events: { value: [] as unknown[] },
  runDetail: { value: undefined as { run: unknown } | undefined },
  runEvents: { value: [] as unknown[] },
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
  useActiveAcpRun: () => ({
    data: mocks.runDetail.value,
    isLoading: false,
    isError: false,
    error: undefined,
  }),
  useAcpRunEvents: () => mocks.runEvents.value,
  useCancelAcpRun: () => ({ mutate: mocks.cancelRunMutate, isPending: false }),
  useReplyAcpCheckpoint: () => ({
    mutate: mocks.replyCheckpointMutate,
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

vi.mock('@/api/acpChat', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAcpChatSessions: () => ({ data: { sessions: mocks.sessions.value }, isLoading: false }),
  useAcpChatSession: (chatId: number | undefined) => {
    mocks.sessionQueryId.value = chatId;
    return {
      data: mocks.activeChat.value,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
  },
  useAcpChatEvents: () => ({ events: mocks.events.value, caughtUp: true }),
  useCreateAcpChatSession: () => ({ mutate: mocks.createMutate, isPending: false }),
  useSendChatMessage: () => ({ mutate: mocks.sendMutate, isPending: false }),
  useChatPermission: () => ({ mutate: mocks.permissionMutate, isPending: false }),
  useCancelChatTurn: () => ({ mutate: mocks.cancelMutate, isPending: false }),
  useCloseChatSession: () => ({ mutate: mocks.closeMutate, isPending: false }),
  useDeleteChatSessionPermanent: () => ({ mutate: mocks.deleteMutate, isPending: false }),
  useUpdateChatConfiguration: () => ({ mutate: mocks.updateConfigMutate, isPending: false }),
}));

import { AcpChatPanel } from '@/components/review/acp-chat/AcpChatPanel';
import { LanguageProvider } from '@/i18n';
import type { AcpChatEvent, AcpChatSession } from '@/api/acpChat';
import type { AcpRun } from '@/api/acp';

function renderWithProviders(ui: React.ReactElement) {
  // 面板内部使用 useQueryClient(删除会话时 patch 缓存),需要 Provider
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>{ui}</LanguageProvider>
    </QueryClientProvider>,
  );
}

function makeChat(overrides: Partial<AcpChatSession>): AcpChatSession {
  return {
    id: 5,
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
    status: 'idle',
    last_error: null,
    latest_seq: 0,
    created_at: '2026-09-08T10:00:00',
    updated_at: '2026-09-08T10:00:00',
    ...overrides,
  };
}

function makeEvent(seq: number, kind: string, payload: Record<string, unknown>): AcpChatEvent {
  return { seq, kind, payload, created_at: '2026-09-08T10:00:00' };
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
    created_at: '2026-09-08T10:00:00',
    finished_at: null,
    ...overrides,
  };
}

describe('AcpChatPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem('ai-marking-locale', 'zh-CN');
    mocks.sessions.value = [];
    mocks.activeChat.value = undefined;
    mocks.sessionQueryId.value = undefined;
    mocks.events.value = [];
    mocks.runDetail.value = undefined;
    mocks.runEvents.value = [];
    mocks.catalog.value = baseCatalog;
  });

  it('空态为 Zed 式输入卡:权限下拉、Fast mode 开关与模型/思考合并下拉', () => {
    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    expect(screen.getByPlaceholderText('随心输入')).toBeInTheDocument();
    expect(screen.getByLabelText('权限档位')).toBeInTheDocument();
    expect(screen.getByLabelText('模型与思考强度')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Fast mode' })).toBeInTheDocument();
    // Fast mode 不可用时禁用并带原因说明
    expect(screen.getByRole('switch', { name: 'Fast mode' })).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Fast mode' })).toHaveAttribute(
      'title',
      '当前账户、模型或 Agent 版本未声明快速模式，暂仅支持标准模式。',
    );
  });

  it('外部下发批改指令时写入输入框,教师可在此基础上编辑', () => {
    const { rerender } = renderWithProviders(
      <AcpChatPanel
        submissionId={42}
        canChat
        prefill={{ text: '请批改 AI-Marking 作业《DTS208TC_CW1_Paper》。', token: 1 }}
      />,
    );

    expect(screen.getByLabelText('消息输入框')).toHaveValue(
      '请批改 AI-Marking 作业《DTS208TC_CW1_Paper》。',
    );

    // 教师编辑后再次下发同一份指令(token 递增):输入框重新被覆盖
    fireEvent.change(screen.getByLabelText('消息输入框'), {
      target: { value: '已编辑的指令' },
    });
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <LanguageProvider>
          <AcpChatPanel
            submissionId={42}
            canChat
            prefill={{ text: '请批改 AI-Marking 作业《DTS208TC_CW1_Paper》。', token: 2 }}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByLabelText('消息输入框')).toHaveValue(
      '请批改 AI-Marking 作业《DTS208TC_CW1_Paper》。',
    );
  });

  it('未下发指令时输入框为空,由教师自行输入', () => {
    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    expect(screen.getByLabelText('消息输入框')).toHaveValue('');
  });

  it('批改运行进行中:展示运行进度并冻结对话输入', () => {
    mocks.runDetail.value = { run: makeRun({ status: 'running' }) };

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    expect(screen.getByText('批改进行中')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消批改' })).toBeInTheDocument();
    expect(screen.getByText('批改运行进行中,对话已暂时冻结。')).toBeInTheDocument();
    // 输入框与开始批改入口都不再可用,避免与 run 争用 MCP 写工具
    expect(screen.queryByLabelText('消息输入框')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始批改' })).not.toBeInTheDocument();
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

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

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

  it('运行配置已排队时提示下一回合生效', () => {
    mocks.runDetail.value = {
      run: makeRun({
        status: 'running',
        pending_codex_config: {
          model_id: 'gpt-5',
          reasoning_effort: 'high',
          speed_mode: 'standard',
        },
        effective_at: 'next_turn',
      }),
    };

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    expect(screen.getByText('新配置将在下一回合生效')).toBeInTheDocument();
  });

  it('模型/思考合并下拉同时列出模型组与思考组', () => {
    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    fireEvent.click(screen.getByLabelText('模型与思考强度'));
    expect(screen.getByRole('listbox', { name: 'Codex 模型' })).toBeInTheDocument();
    expect(screen.getByRole('listbox', { name: '思考强度' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Codex 默认/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '中等' })).toBeInTheDocument();
  });

  it('工具栏下拉可选权限档位,并进入创建请求载荷', async () => {
    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    fireEvent.click(screen.getByLabelText('权限档位'));
    fireEvent.click(await screen.findByRole('option', { name: '自动批准' }));

    const input = screen.getByLabelText('消息输入框');
    fireEvent.change(input, { target: { value: '总结一下批改意见' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(mocks.createMutate).toHaveBeenCalledWith(
        {
          permission_mode: 'auto_review',
          codex_config: {
            model_id: 'codex-default',
            reasoning_effort: 'medium',
            speed_mode: 'standard',
            fast_confirmed: false,
          },
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });
  });

  it('空态输入首条消息即创建会话,创建成功后自动发出首条消息', async () => {
    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    const input = screen.getByLabelText('消息输入框');
    fireEvent.change(input, { target: { value: '总结一下批改意见' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(mocks.createMutate).toHaveBeenCalled();
    });

    // 模拟创建成功:面板切到聊天态,activeChatId effect 自动发出登记的首条消息
    await act(async () => {
      mocks.activeChat.value = makeChat({ id: 9, status: 'idle' });
      mocks.createMutate.mock.calls[0][1].onSuccess({ id: 9 });
    });

    await waitFor(() => {
      expect(mocks.sendMutate).toHaveBeenCalledWith('总结一下批改意见', expect.anything());
    });
  });

  it('Fast mode 不可用时禁用并说明原因', async () => {
    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);
    const sw = screen.getByRole('switch', { name: 'Fast mode' });
    expect(sw).toBeDisabled();
    expect(sw).toHaveAttribute(
      'title',
      '当前账户、模型或 Agent 版本未声明快速模式，暂仅支持标准模式。',
    );
  });

  it('已有会话时输入消息直接发送', () => {
    mocks.sessions.value = [makeChat({})];
    mocks.activeChat.value = makeChat({ status: 'idle', latest_seq: 2 });
    mocks.events.value = [
      makeEvent(1, 'user_message', { text: '总结一下批改意见' }),
      makeEvent(2, 'agent_message_chunk', { text: '报告结构完整,建议…' }),
    ];

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    expect(screen.getByText('总结一下批改意见')).toBeInTheDocument();

    const input = screen.getByLabelText('消息输入框');
    fireEvent.change(input, { target: { value: '第二段为什么扣分?' } });
    fireEvent.click(screen.getByRole('button', { name: /发送/ }));
    expect(mocks.sendMutate).toHaveBeenCalledWith('第二段为什么扣分?', expect.anything());
  });

  it('已有会话的工具栏可热切换权限档位,走 PATCH 配置接口', () => {
    mocks.sessions.value = [makeChat({})];
    mocks.activeChat.value = makeChat({ status: 'idle' });

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    fireEvent.click(screen.getByLabelText('权限档位'));
    fireEvent.click(screen.getByRole('option', { name: '自动批准' }));

    expect(mocks.updateConfigMutate).toHaveBeenCalledWith(
      { permission_mode: 'auto_review' },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
  });

  it('已有会话的工具栏可热切换模型,走 PATCH 配置接口', () => {
    mocks.sessions.value = [makeChat({})];
    mocks.activeChat.value = makeChat({ status: 'idle' });

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    fireEvent.click(screen.getByLabelText('模型与思考强度'));
    fireEvent.click(screen.getByRole('option', { name: /Codex 默认/ }));

    expect(mocks.updateConfigMutate).toHaveBeenCalledWith(
      {
        codex_config: {
          model_id: 'codex-default',
          reasoning_effort: 'medium',
          speed_mode: 'standard',
          fast_confirmed: false,
        },
      },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
  });

  it('waiting_permission 展示权限卡,批准携带第一个 allow 选项', () => {
    mocks.sessions.value = [makeChat({ status: 'waiting_permission' })];
    mocks.activeChat.value = makeChat({ status: 'waiting_permission', latest_seq: 3 });
    mocks.events.value = [
      makeEvent(1, 'user_message', { text: '运行一下' }),
      makeEvent(2, 'agent_message_chunk', { text: '好的' }),
      makeEvent(3, 'permission_request', {
        permission_id: 'abc12345',
        kind: 'execute',
        title: '写入评分建议',
        options: [
          { option_id: 'allow', kind: 'allow_once', title: '允许' },
          { option_id: 'reject', kind: 'reject_once', title: '拒绝' },
        ],
      }),
    ];

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    expect(screen.getByText('助手请求批准')).toBeInTheDocument();
    expect(screen.getByText('写入评分建议')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /拒绝/ }));
    expect(mocks.permissionMutate).toHaveBeenCalledWith(
      { permission_id: 'abc12345', allow: false },
      expect.anything(),
    );

    fireEvent.click(screen.getByRole('button', { name: /批准/ }));
    expect(mocks.permissionMutate).toHaveBeenCalledWith(
      { permission_id: 'abc12345', allow: true, option_id: 'allow' },
      expect.anything(),
    );
  });

  it('running 时发送按钮变停止,输入禁发但可打字', () => {
    mocks.sessions.value = [makeChat({ status: 'running' })];
    mocks.activeChat.value = makeChat({ status: 'running' });
    mocks.events.value = [makeEvent(1, 'user_message', { text: '继续' })];

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    // 顶部状态条已移除:状态只通过停止按钮体现
    expect(screen.queryByText('回复中')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /停止/ })).toBeInTheDocument();
    // 输入框可打字,但发送按钮已切换为停止,不存在可用的发送按钮
    expect(screen.getByLabelText('消息输入框')).toBeEnabled();
    expect(screen.queryByRole('button', { name: /发送/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /停止/ }));
    expect(mocks.cancelMutate).toHaveBeenCalledWith(undefined, expect.anything());
  });

  it('closed 会话只读:不显示输入框;省略号菜单仍提供删除对话', () => {
    mocks.sessions.value = [makeChat({ status: 'closed' })];
    mocks.activeChat.value = makeChat({ status: 'closed' });
    mocks.events.value = [makeEvent(1, 'user_message', { text: '历史消息' })];

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    // 顶部状态条已移除:关闭态以无输入框体现
    expect(screen.queryByText('已关闭')).not.toBeInTheDocument();
    expect(screen.getByText('历史消息')).toBeInTheDocument();
    expect(screen.queryByLabelText('消息输入框')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: '删除对话' }));

    expect(screen.getByText('将永久删除该对话的聊天记录、事件转录与专属工作区，此操作不可恢复。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }));
    expect(mocks.deleteMutate).toHaveBeenCalledWith(5, expect.anything());
  });

  it('取消删除不触发删除请求', () => {
    mocks.sessions.value = [makeChat({})];
    mocks.activeChat.value = makeChat({ status: 'idle' });

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: '删除对话' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(mocks.deleteMutate).not.toHaveBeenCalled();
  });

  it('会话历史列表行悬停出现删除按钮,可直接删除未打开的会话', async () => {
    mocks.sessions.value = [makeChat({ id: 5 }), makeChat({ id: 7, status: 'closed' })];
    mocks.activeChat.value = makeChat({ id: 5, status: 'idle' });

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);

    fireEvent.click(screen.getByRole('button', { name: '会话历史' }));
    fireEvent.click(screen.getByRole('button', { name: '删除对话 #7' }));

    expect(screen.getByText('将永久删除该对话的聊天记录、事件转录与专属工作区，此操作不可恢复。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }));
    expect(mocks.deleteMutate).toHaveBeenCalledWith(7, expect.anything());

    // 删除非当前会话:视图停留在 #5,不切回新对话
    const [, options] = mocks.deleteMutate.mock.calls.at(-1)!;
    await act(async () => {
      (options as { onSuccess: (data: unknown) => void }).onSuccess({});
    });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByLabelText('消息输入框')).toBeInTheDocument();
  });

  it('canChat=false 时显示禁用提示', () => {
    mocks.sessions.value = [makeChat({})];
    mocks.activeChat.value = makeChat({ status: 'idle' });
    mocks.events.value = [];

    renderWithProviders(<AcpChatPanel submissionId={42} canChat={false} />);

    expect(
      screen.getByText('批改流程进行中,暂不可继续对话。'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('消息输入框')).toBeDisabled();
  });

  it('删除当前活跃会话后自动选中下一会话,不再选中已删除会话', async () => {
    const chat5 = makeChat({ id: 5 });
    const chat7 = makeChat({ id: 7, status: 'closed' });
    mocks.sessions.value = [chat5, chat7];
    mocks.activeChat.value = chat5;

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);
    // 初始自动选中 #5
    await waitFor(() => expect(mocks.sessionQueryId.value).toBe(5));

    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: '删除对话' }));
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }));
    expect(mocks.deleteMutate).toHaveBeenCalledWith(5, expect.anything());

    // 删除成功且列表刷新后 #5 消失(等效真实缓存 patch + refetch)
    await act(async () => {
      mocks.sessions.value = [chat7];
      mocks.activeChat.value = chat7;
      mocks.deleteMutate.mock.calls.at(-1)![1].onSuccess({});
    });

    // 自动选中 #7,而不是用删除前的旧列表重选已删除的 #5(404 → 永久 spinner)
    await waitFor(() => expect(mocks.sessionQueryId.value).toBe(7));
  });

  it('删除最后一个会话后回到新对话草稿态', async () => {
    mocks.sessions.value = [makeChat({ id: 5 })];
    mocks.activeChat.value = makeChat({ id: 5, status: 'idle' });

    renderWithProviders(<AcpChatPanel submissionId={42} canChat />);
    await waitFor(() => expect(mocks.sessionQueryId.value).toBe(5));

    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: '删除对话' }));
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }));

    await act(async () => {
      mocks.sessions.value = [];
      mocks.deleteMutate.mock.calls.at(-1)![1].onSuccess({});
    });

    // 列表为空:回到新对话草稿态,输入框可见
    expect(await screen.findByPlaceholderText('随心输入')).toBeInTheDocument();
    expect(mocks.sessionQueryId.value).toBeUndefined();
  });
});
