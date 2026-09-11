import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  createMutate: vi.fn(),
  analyzeZip: vi.fn(),
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
}));

vi.mock('@/lib/zipPreview', async () => {
  const actual = await vi.importActual<typeof import('@/lib/zipPreview')>(
    '@/lib/zipPreview',
  );
  return { ...actual, analyzeZip: mocks.analyzeZip };
});

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}));

vi.mock('@/api/submissions', () => ({
  useCreateSubmission: () => ({ mutate: mocks.createMutate, isPending: false }),
}));

vi.mock('@/api/questions', () => ({
  useQuestions: () => ({
    data: {
      items: [
        { id: 'q1', name: '编程题 1' },
        { id: 'q2', name: '编程题 2' },
      ],
      total: 2,
      skip: 0,
      limit: 50,
    },
  }),
}));

vi.mock('@/api/acp', () => ({
  useAcpAgents: () => ({ data: { agents: mocks.agents.value }, isLoading: false }),
}));

import UploadSubmissionPage from '@/pages/UploadSubmissionPage';
import { LanguageProvider } from '@/i18n';
import { Toaster } from '@/components/ui/sonner';

/** 真实 analyzeZip,供默认透传 mock 使用(beforeAll 中赋值)。 */
let realAnalyzeZip: typeof import('@/lib/zipPreview').analyzeZip;

beforeAll(async () => {
  const actual = await vi.importActual<typeof import('@/lib/zipPreview')>(
    '@/lib/zipPreview',
  );
  realAnalyzeZip = actual.analyzeZip;
});

function renderWithProviders(ui: React.ReactElement) {
  return render(
    <LanguageProvider>
      {/* Toaster 放在页面之前：真实导航时它已随 App 挂载，页面 effect 弹 toast 时订阅已存在 */}
      <Toaster richColors position="top-center" />
      {ui}
    </LanguageProvider>,
  );
}

function pdfFile(): File {
  return new File(['pdf-bytes'], 'report.pdf', { type: 'application/pdf' });
}

describe('UploadSubmissionPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: 'zh-CN',
    });
    window.localStorage.setItem('ai-marking-locale', 'zh-CN');
    window.sessionStorage.clear();
    // 默认透传真实 analyzeZip;竞态用例用 mockImplementationOnce 接管
    mocks.analyzeZip.mockReset();
    mocks.analyzeZip.mockImplementation(
      (file: File) => realAnalyzeZip(file),
    );
    mocks.createMutate.mockImplementation((_payload: unknown, opts?: { onSuccess?: (res: unknown) => void }) => {
      opts?.onSuccess?.({ id: 5, status: 'pending' });
    });
  });

  it('渲染题目下拉、评分方式二选一;权限档位不在上传页选择', () => {
    renderWithProviders(<UploadSubmissionPage />);

    expect(screen.getByLabelText('选择题目')).toBeInTheDocument();
    expect(screen.getByText('使用 ACP 自动批改')).toBeInTheDocument();
    expect(screen.getByText('使用 MCP 等待外部编程助手')).toBeInTheDocument();
    // 权限档位已移到批改会话工具栏热切换,上传页不再渲染
    expect(screen.queryByLabelText('权限档位')).not.toBeInTheDocument();
    expect(
      screen.getByText('默认以 Ask for approval 档位启动，批改会话中可随时调整模型与权限。'),
    ).toBeInTheDocument();
    // 运行配置入口已移除:不再渲染模型/思考/速度选择
    expect(screen.queryByLabelText('Codex 模型')).not.toBeInTheDocument();
  });

  it('q1.py 自动填小题号;ACP 提交写入自动启动标记并跳转详情页', () => {
    renderWithProviders(<UploadSubmissionPage />);

    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });
    const pdf = pdfFile();
    fireEvent.change(screen.getByLabelText('选择作业文件'), {
      target: { files: [pdf] },
    });
    const code = new File(['print(1)'], 'q1.py', { type: 'text/x-python' });
    fireEvent.change(screen.getByLabelText('选择代码文件输入框'), {
      target: { files: [code] },
    });

    // 文件名 q1.py 自动推导小题号 1
    expect(screen.getByLabelText('小题号 q1.py')).toHaveValue(1);

    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(mocks.createMutate).toHaveBeenCalledWith(
      {
        file: pdf,
        questionId: 'q1',
        codeInputs: [{ file: code, questionNumber: 1, entrypoint: true }],
      },
      expect.anything(),
    );
    expect(window.sessionStorage.getItem('acp-assistant-prefill:5')).toBe('1');
    expect(mocks.navigate).toHaveBeenCalledWith('/review/5');
  });

  it('MCP 模式提交不写助手预填标记,仍跳转详情页', () => {
    renderWithProviders(<UploadSubmissionPage />);

    fireEvent.click(screen.getByText('使用 MCP 等待外部编程助手'));
    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });
    const pdf = pdfFile();
    fireEvent.change(screen.getByLabelText('选择作业文件'), {
      target: { files: [pdf] },
    });

    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(mocks.createMutate).toHaveBeenCalledWith(
      expect.objectContaining({ questionId: 'q1', file: pdf }),
      expect.anything(),
    );
    expect(window.sessionStorage.getItem('acp-assistant-prefill:5')).toBeNull();
    expect(mocks.navigate).toHaveBeenCalledWith('/review/5');
  });

  it('未选择作业文件 时禁用提交按钮', () => {
    renderWithProviders(<UploadSubmissionPage />);

    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });

    expect(
      screen.getByRole('button', { name: '开始上传' }),
    ).toBeDisabled();
    expect(mocks.createMutate).not.toHaveBeenCalled();
  });

  it('携带 ocr-ready-questions 标记进入时预选刚识别完成的题目', async () => {
    window.sessionStorage.setItem(
      'ocr-ready-questions',
      JSON.stringify({ names: ['编程题 2'], at: Date.now() }),
    );
    renderWithProviders(<UploadSubmissionPage />);

    expect(screen.getByLabelText('选择题目')).toHaveValue('q2');
    expect(
      await screen.findByText(
        (_, element) =>
          element?.textContent === '「编程题 2」识别完成，已为你预选，可直接上传作业答案' &&
          element.children.length === 0,
      ),
    ).toBeInTheDocument();
    // 标记为一次性，消费后清除
    expect(window.sessionStorage.getItem('ocr-ready-questions')).toBeNull();
  });

  it('不同小题可各选一个入口文件;同小题内单选互斥', () => {
    renderWithProviders(<UploadSubmissionPage />);

    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });
    fireEvent.change(screen.getByLabelText('选择作业文件'), {
      target: { files: [pdfFile()] },
    });
    const files = [1, 2, 3, 4].map(
      (n) => new File([`print(${n})`], `task${n}.py`, { type: 'text/x-python' }),
    );
    fireEvent.change(screen.getByLabelText('选择代码文件输入框'), {
      target: { files },
    });

    // 手动补齐小题号 1~4(task 命名不会自动推导)
    for (let n = 1; n <= 4; n += 1) {
      fireEvent.change(screen.getByLabelText(`小题号 task${n}.py`), {
        target: { value: String(n) },
      });
    }
    // 每个文件自动成为所在小题的入口:四个圆点全部选中
    for (let n = 1; n <= 4; n += 1) {
      expect(screen.getByLabelText(`入口文件 task${n}.py`)).toBeChecked();
    }

    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(mocks.createMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        codeInputs: [
          { file: files[0], questionNumber: 1, entrypoint: true },
          { file: files[1], questionNumber: 2, entrypoint: true },
          { file: files[2], questionNumber: 3, entrypoint: true },
          { file: files[3], questionNumber: 4, entrypoint: true },
        ],
      }),
      expect.anything(),
    );
  });

  it('同一小题的多个文件单选互斥,切换入口后旧入口变普通文件', () => {
    renderWithProviders(<UploadSubmissionPage />);

    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });
    fireEvent.change(screen.getByLabelText('选择作业文件'), {
      target: { files: [pdfFile()] },
    });
    const main = new File(['print(1)'], 'main.py', { type: 'text/x-python' });
    const utils = new File(['def help(): pass'], 'utils.py', { type: 'text/x-python' });
    fireEvent.change(screen.getByLabelText('选择代码文件输入框'), {
      target: { files: [main, utils] },
    });
    // 两个文件都归到小题 1
    fireEvent.change(screen.getByLabelText('小题号 main.py'), {
      target: { value: '1' },
    });
    fireEvent.change(screen.getByLabelText('小题号 utils.py'), {
      target: { value: '1' },
    });

    const mainRadio = screen.getByLabelText('入口文件 main.py');
    const utilsRadio = screen.getByLabelText('入口文件 utils.py');
    // 同组先到先得:main.py 自动担任入口,utils.py 未选中
    expect(mainRadio).toBeChecked();
    expect(utilsRadio).not.toBeChecked();

    fireEvent.click(utilsRadio);
    expect(utilsRadio).toBeChecked();
    expect(mainRadio).not.toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(mocks.createMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        codeInputs: [
          { file: main, questionNumber: 1, entrypoint: false },
          { file: utils, questionNumber: 1, entrypoint: true },
        ],
      }),
      expect.anything(),
    );
  });

  it('小题号非法时拦截提交', async () => {    renderWithProviders(<UploadSubmissionPage />);

    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });
    fireEvent.change(screen.getByLabelText('选择作业文件'), {
      target: { files: [pdfFile()] },
    });
    const code = new File(['print(1)'], 'main.c', { type: 'text/x-c' });
    fireEvent.change(screen.getByLabelText('选择代码文件输入框'), {
      target: { files: [code] },
    });
    // 清空小题号 → 非法
    fireEvent.change(screen.getByLabelText('小题号 main.c'), {
      target: { value: '' },
    });

    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(mocks.createMutate).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/的小题号必须是正整数/),
    ).toBeInTheDocument();
  });

  it('选择 ZIP 后显示预览、隐藏散装代码卡片;提交不带 codeInputs', async () => {
    const zip = makeZipFile([
      { name: '报告.pdf', data: '%PDF-1.4' },
      { name: 'q1.py', data: 'print(1)' },
    ], 'assignment.zip');

    renderWithProviders(<UploadSubmissionPage />);
    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });
    fireEvent.change(screen.getByLabelText('选择作业文件'), {
      target: { files: [zip] },
    });

    // 预览卡片出现:报告与代码分类展示
    expect(await screen.findByText('ZIP 内容预览')).toBeInTheDocument();
    expect(await screen.findByText('报告.pdf')).toBeInTheDocument();
    expect(screen.getByText(/q1\.py/)).toBeInTheDocument();
    // 散装代码卡片隐藏
    expect(screen.queryByText('代码文件（可选）')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(mocks.createMutate).toHaveBeenCalledWith(
      expect.objectContaining({ file: zip, questionId: 'q1' }),
      expect.anything(),
    );
    const payload = mocks.createMutate.mock.calls[0][0];
    expect(payload.codeInputs).toBeUndefined();
  });

  it('选择含错误的 ZIP(缺报告)时禁用提交并展示错误', async () => {
    const zip = makeZipFile([
      { name: 'q1.py', data: 'print(1)' },
    ], 'bad.zip');

    renderWithProviders(<UploadSubmissionPage />);
    fireEvent.change(screen.getByLabelText('选择题目'), {
      target: { value: 'q1' },
    });
    fireEvent.change(screen.getByLabelText('选择作业文件'), {
      target: { files: [zip] },
    });

    expect(await screen.findByText('ZIP 中缺少报告 PDF')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始上传' })).toBeDisabled();
    expect(mocks.createMutate).not.toHaveBeenCalled();
  });

  it('连续选择两个 ZIP 时,较慢的旧解析不覆盖新文件预览', async () => {
    let resolveFirst: (preview: unknown) => void = () => {};
    const firstPromise = new Promise((resolve) => {
      resolveFirst = resolve;
    });
    const previewA = {
      report: { name: 'old-report.pdf', size: 1 },
      code: [],
      datasets: [],
      skipped: [],
      errors: [],
    };
    const previewB = {
      report: { name: 'new-report.pdf', size: 1 },
      code: [],
      datasets: [],
      skipped: [],
      errors: [],
    };
    mocks.analyzeZip
      .mockImplementationOnce(() => firstPromise)
      .mockImplementationOnce(async () => previewB);

    renderWithProviders(<UploadSubmissionPage />);
    const input = screen.getByLabelText('选择作业文件');
    fireEvent.change(input, {
      target: { files: [new File(['a'], 'a.zip', { type: 'application/zip' })] },
    });
    fireEvent.change(input, {
      target: { files: [new File(['b'], 'b.zip', { type: 'application/zip' })] },
    });

    // 第二个(新)ZIP 的预览先就绪
    expect(await screen.findByText('new-report.pdf')).toBeInTheDocument();
    // 旧解析此刻才完成,不得覆盖新预览,也不得清空新选择的文件
    resolveFirst(previewA);
    await waitFor(() =>
      expect(screen.getByText('new-report.pdf')).toBeInTheDocument(),
    );
    expect(screen.queryByText('old-report.pdf')).not.toBeInTheDocument();
  });
});

/** 构造最小合法 ZIP(本地文件头 + 中央目录 + EOCD),与 zipPreview 测试同款。 */
function makeZipFile(
  entries: { name: string; data: string }[],
  filename: string,
): File {
  const encoder = new TextEncoder();
  const u16 = (v: number) => new Uint8Array([v & 0xff, (v >> 8) & 0xff]);
  const u32 = (v: number) =>
    new Uint8Array([v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >>> 24) & 0xff]);
  let offset = 0;
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  for (const { name, data } of entries) {
    const nameBytes = encoder.encode(name);
    const dataBytes = encoder.encode(data);
    localParts.push(
      new Uint8Array([
        ...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
        ...u32(0), ...u32(dataBytes.length), ...u32(dataBytes.length),
        ...u16(nameBytes.length), ...u16(0),
      ]),
      nameBytes,
      dataBytes,
    );
    centralParts.push(
      new Uint8Array([
        ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0),
        ...u16(0), ...u32(0), ...u32(dataBytes.length), ...u32(dataBytes.length),
        ...u16(nameBytes.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0),
        ...u32(offset),
      ]),
      nameBytes,
    );
    offset += nameBytes.length + dataBytes.length + 30;
  }
  const cdStart = offset;
  const cdLen = centralParts.reduce((sum, part) => sum + part.length, 0);
  const eocd = new Uint8Array([
    ...u32(0x06054b50), ...u16(0), ...u16(0),
    ...u16(entries.length), ...u16(entries.length),
    ...u32(cdLen), ...u32(cdStart), ...u16(0),
  ]);
  const parts = [...localParts, ...centralParts, eocd];
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const all = new Uint8Array(total);
  let cursor = 0;
  for (const part of parts) {
    all.set(part, cursor);
    cursor += part.length;
  }
  return new File([all], filename, { type: 'application/zip' });
}
