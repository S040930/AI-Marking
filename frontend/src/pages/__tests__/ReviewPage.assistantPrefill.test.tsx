import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { SubmissionDetail } from '@/api/submissions';

const mocks = vi.hoisted(() => ({
  submission: { value: undefined as unknown },
  createRunMutate: vi.fn(),
}));

/**
 * jsdom 没有 ResizeObserver,而批改页用它判断窗口是否够宽以展开三栏。
 * 这里同步回调一个 1600px 宽的容器,模拟桌面端 —— 助手面板不会被自动收起,
 * 否则整页测试里根本渲染不到右侧对话面板。
 */
class ResizeObserverStub {
  // 不用参数属性(constructor(private readonly x)):项目开了 erasableSyntaxOnly
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe() {
    this.callback(
      [{ contentRect: { width: 1600 } }] as unknown as ResizeObserverEntry[],
      this as unknown as ResizeObserver,
    );
  }

  unobserve() {}

  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub);

vi.mock('@/components/PdfViewer', () => ({
  PdfViewer: () => null,
  PdfViewerPlaceholder: () => null,
}));

vi.mock('@/api/submissions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/submissions')>()),
  useSubmissionStatus: () => ({
    data: {
      id: 42,
      status: 'awaiting_mcp',
      original_filename: 'DTS208TC_CW1_Paper.pdf',
      uploaded_at: '2026-09-10T10:00:00',
    },
  }),
  useSubmission: () => ({ data: mocks.submission.value, isLoading: false }),
  useFinalizeSubmission: () => ({ mutate: vi.fn(), isPending: false }),
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
  useAcpAgents: () => ({
    data: {
      agents: [
        {
          agent_id: 'codex-acp',
          display_name: 'Codex',
          whitelist_package: '@agentclientprotocol/codex-acp',
          distributions: ['npx'],
          installed_version: '1.0.0',
          available_version: '1.0.0',
          connection_status: 'ready',
        },
      ],
    },
    isLoading: false,
  }),
  useActiveAcpRun: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    error: { response: { status: 404 } },
  }),
  useAcpRunEvents: () => [],
  // 保留 hook 只为断言「开始批改不再直接发起 run」
  useCreateAcpRun: () => ({ mutate: mocks.createRunMutate, isPending: false }),
  useCancelAcpRun: () => ({ mutate: vi.fn(), isPending: false }),
  useReplyAcpCheckpoint: () => ({ mutate: vi.fn(), isPending: false }),
  useCodexConfiguration: () => ({
    data: baseCatalog,
    isLoading: false,
    isFetching: false,
  }),
  useUpdateRunCodexConfiguration: () => ({ mutate: vi.fn(), isPending: false }),
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
  useAcpChatSessions: () => ({ data: { sessions: [] }, isLoading: false }),
  useAcpChatSession: () => ({ data: undefined, isLoading: false }),
  useAcpChatEvents: () => ({ events: [], caughtUp: true }),
  useCreateAcpChatSession: () => ({ mutate: vi.fn(), isPending: false }),
  useSendChatMessage: () => ({ mutate: vi.fn(), isPending: false }),
  useChatPermission: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelChatTurn: () => ({ mutate: vi.fn(), isPending: false }),
  useCloseChatSession: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteChatSessionPermanent: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateChatConfiguration: () => ({ mutate: vi.fn(), isPending: false }),
}));

import ReviewPage from '@/pages/ReviewPage';
import { LanguageProvider } from '@/i18n';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const SUBMISSION = {
  id: 42,
  original_filename: 'DTS208TC_CW1_Paper.pdf',
  question_id: 'dts208tc-cw1-paper',
  question_name: 'DTS208TC_CW1_Paper',
  status: 'awaiting_mcp',
  code_files: [],
  code_input_files: [],
  assessment_suggestion: null,
  assessment_review: null,
  details: null,
  feedback: null,
  ocr_text: null,
  question_ocr_text: null,
  reviewed_by: null,
  reviewed_at: null,
  error_message: null,
} as unknown as SubmissionDetail;

function renderPage() {
  // ReviewPage 内的 AcpChatPanel 使用 useQueryClient(删除会话时 patch 缓存)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <MemoryRouter initialEntries={['/review/42']}>
          <Routes>
            <Route path="/review/:id" element={<ReviewPage />} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

function composerValue(): string {
  return (screen.getByLabelText('消息输入框') as HTMLTextAreaElement).value;
}

describe('ReviewPage 批改指令下发', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem('ai-marking-locale', 'zh-CN');
    window.sessionStorage.clear();
    mocks.submission.value = SUBMISSION;
  });

  it('无预填标记时不自动打开助手,输入框留空给教师自己写', () => {
    renderPage();

    expect(screen.queryByLabelText('消息输入框')).not.toBeInTheDocument();
  });

  it('上传页留下的标记:进详情页即打开助手并填入批改指令,标记随即清理', () => {
    window.sessionStorage.setItem('acp-assistant-prefill:42', '1');

    renderPage();

    // 标记只在首次进入生效,清掉后再刷新页面不会重复填指令
    expect(window.sessionStorage.getItem('acp-assistant-prefill:42')).toBeNull();
    expect(composerValue()).toContain('DTS208TC_CW1_Paper');
    expect(composerValue()).toContain('submission_id=42');
    // 提示词只做入口:完整流程由助手读取工作区的 skill.md
    expect(composerValue()).toContain('skill.md');
  });

  it('点中间栏「开始批改」:助手替换中间整栏(批改模式两栏),不再直接发起批改 run', () => {
    renderPage();

    expect(screen.getByText('启动 Codex 自动批改')).toBeInTheDocument();
    // 打开前助手面板未挂载
    expect(screen.queryByLabelText('消息输入框')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '开始批改' }));

    // 批改模式:启动卡片与 MCP 恢复兜底整栏让位给 AI 助手,只剩
    // 左侧作业预览 + 右侧 Codex ACP 两栏
    expect(screen.queryByText('启动 Codex 自动批改')).not.toBeInTheDocument();
    expect(screen.queryByText('等待编程助手评分')).not.toBeInTheDocument();
    expect(composerValue()).toContain('DTS208TC_CW1_Paper');
    expect(composerValue()).toContain('submission_id=42');
    // 提示词只做入口:完整流程(含评分保存契约)由助手读取 skill.md
    expect(composerValue()).toContain('skill.md');
    // 指令只是预填,发送动作与批改发起都由教师在助手里决定
    expect(mocks.createRunMutate).not.toHaveBeenCalled();

    // awaiting_mcp 下助手工具栏的模型/思考/权限选择器可用,教师据此确认配置
    expect(screen.getByLabelText('权限档位')).toBeInTheDocument();
    expect(screen.getByLabelText('模型与思考强度')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Fast mode' })).toBeInTheDocument();
  });

  it('点「开始批改」后整个批改入口视图收起,不再显示 MCP 恢复兜底', () => {
    renderPage();

    expect(screen.getByText('等待编程助手评分')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开始批改' }));

    expect(screen.queryByText('等待编程助手评分')).not.toBeInTheDocument();
    expect(screen.getByText('Codex ACP')).toBeInTheDocument();
  });
});
