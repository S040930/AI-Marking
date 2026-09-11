import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import dayjs from 'dayjs';
import { Loader2, AlertCircle, MessageSquare } from 'lucide-react';
import {
  useSubmission,
  useSubmissionStatus,
  useFinalizeSubmission,
  canLoadSubmissionDetail,
  isProcessing,
  type SubmissionDetail,
  type FinalizePayload,
} from '@/api/submissions';
import { STATUS_TEXT } from '@/lib/submissionStatus';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { PdfViewer, PdfViewerPlaceholder } from '@/components/PdfViewer';
import ManualReviewPanel from '@/components/ManualReviewPanel';
import { AcpMarkingPanel } from '@/components/review/AcpMarkingPanel';
import { McpAuditBanner } from '@/components/review/McpAuditBanner';
import { AssessmentReviewCard } from '@/components/review/AssessmentReviewCard';
import { CodeEvidencePanel } from '@/components/review/CodeEvidencePanel';
import { FailedRecoveryPanel } from '@/components/review/FailedRecoveryPanel';
import { AcpChatPanel } from '@/components/review/acp-chat/AcpChatPanel';
import { normalizeSuggestion } from '@/components/review/normalizeSuggestion';
import { buildAcpChatGradingPrompt } from '@/lib/gradingPrompt';
import { useLanguage } from '@/i18n';
import { PageLoading } from '@/components/common/PageLoading';
import { BackToHistoryButton } from '@/components/common/BackToHistoryButton';

function ReviewContent({ data }: { data: SubmissionDetail }) {
  const navigate = useNavigate();
  const { locale, t } = useLanguage();
  const splitContainerRef = useRef<HTMLElement>(null);
  const draggingRef = useRef(false);
  const chatDraggingRef = useRef(false);
  const prefillTokenRef = useRef(0);
  const [leftPercent, setLeftPercent] = useState(40);
  const [chatPercent, setChatPercent] = useState(25);
  const [evidenceTab, setEvidenceTab] = useState<'report' | 'code'>('report');
  const [isDragging, setIsDragging] = useState(false);
  const [isChatDragging, setIsChatDragging] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [isChatAutoCollapsed, setIsChatAutoCollapsed] = useState(false);
  // 下发给 AI 助手的批改指令:token 递增以支持重复下发同一份指令。
  const [assistantPrefill, setAssistantPrefill] = useState<{
    text: string;
    token: number;
  } | null>(null);
  const isReadOnly = data.status === 'reviewed';
  const isFailed = data.status === 'failed';
  // 对话面板对 awaiting_mcp 也开放:教师在助手里确认模型、思考强度与权限档位,
  // 手动发送批改指令。与批改 run 的互斥由后端「存在活跃 run 即拒绝发消息」与
  // 前端运行期冻结对话共同保证。
  const canChat = ['awaiting_mcp', 'ready_for_review', 'reviewed', 'failed'].includes(data.status);

  const reviewerName = 'Teacher';

  const suggestion = normalizeSuggestion(data);
  const finalizeMutation = useFinalizeSubmission(data.id);

  /** 打开 AI 助手并填入批改指令;窄屏下助手嵌入中间栏替换批改入口卡片。 */
  const startInAssistant = () => {
    prefillTokenRef.current += 1;
    setAssistantPrefill({
      text: buildAcpChatGradingPrompt(
        {
          questionName: data.question_name,
          submissionId: data.id,
        },
        locale,
      ),
      token: prefillTokenRef.current,
    });
    setChatOpen(true);
  };

  // 上传页选了 ACP 时留下的意图标记:进入详情页即打开助手并预填批改指令。
  useEffect(() => {
    const key = `acp-assistant-prefill:${data.id}`;
    if (sessionStorage.getItem(key) === null) return;
    sessionStorage.removeItem(key);
    startInAssistant();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.id]);

  const handleFinalize = (payload: FinalizePayload) => {
    finalizeMutation.mutate(
      { ...payload, reviewer_name: reviewerName },
      {
        onSuccess: () => {
          toast.success(t('评分已提交'));
          navigate(`/result/${data.id}`);
        },
        onError: () => toast.error(t('提交失败，请稍后重试')),
      },
    );
  };

  const updateSplitFromPointer = (clientX: number) => {
    const container = splitContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const nextPercent = ((clientX - rect.left) / rect.width) * 100;
    setLeftPercent(Math.min(65, Math.max(25, nextPercent)));
  };

  const updateChatSplitFromPointer = (clientX: number) => {
    const container = splitContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const chatWidth = rect.right - clientX;
    const nextPercent = (chatWidth / rect.width) * 100;
    setChatPercent(Math.min(35, Math.max(18, nextPercent)));
  };

  const handleDividerPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (window.matchMedia('(min-width: 1024px)').matches === false) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    draggingRef.current = true;
    setIsDragging(true);
    updateSplitFromPointer(event.clientX);
  };

  const handleDividerPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    updateSplitFromPointer(event.clientX);
  };

  const stopDragging = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    draggingRef.current = false;
    setIsDragging(false);
  };

  const handleChatDividerPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (window.matchMedia('(min-width: 1024px)').matches === false) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    chatDraggingRef.current = true;
    setIsChatDragging(true);
    updateChatSplitFromPointer(event.clientX);
  };

  const handleChatDividerPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!chatDraggingRef.current) return;
    updateChatSplitFromPointer(event.clientX);
  };

  const stopChatDragging = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    chatDraggingRef.current = false;
    setIsChatDragging(false);
  };

  const handleDividerKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const delta = event.key === 'ArrowLeft' ? -2 : 2;
    setLeftPercent((current) => Math.min(65, Math.max(25, current + delta)));
  };

  const handleChatDividerKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const delta = event.key === 'ArrowLeft' ? 2 : -2;
    setChatPercent((current) => Math.min(35, Math.max(18, current + delta)));
  };

  // 窗口过窄放不下三栏最小尺寸时，自动收起聊天面板
  useEffect(() => {
    const el = splitContainerRef.current;
    if (!el) return;
    // 推导：PDF 最小 25% + 中间 320 + 聊天 360 + 两条分隔 18 → 约 931px，留余量取 1000
    const MIN_WIDTH_FOR_THREE_PANES = 1000;
    const ro = new ResizeObserver(([entry]) => {
      setIsChatAutoCollapsed(entry.contentRect.width < MIN_WIDTH_FOR_THREE_PANES);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 聊天面板需同时满足：用户主动开启 + 窗口宽度足以容纳三栏
  const showChat = chatOpen && !isChatAutoCollapsed;
  // awaiting_mcp 下指令已下发:两栏布局,左作业预览右 Codex 助手,批改入口卡片
  // 整栏让位;窄窗口放不下两栏时退化为在中间栏内嵌助手。
  const gradingMode = data.status === 'awaiting_mcp' && showChat;
  const embeddedChat = chatOpen && isChatAutoCollapsed && data.status === 'awaiting_mcp';

  return (
    <div className="flex h-svh flex-col overflow-hidden bg-slate-100/80">
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-slate-300/80 bg-white px-4">
        <BackToHistoryButton />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          {data.original_filename}
        </span>
        {canChat ? (
          <Button
            size="sm"
            variant={chatOpen ? 'secondary' : 'ghost'}
            className="h-8 shrink-0 px-2.5 text-xs"
            aria-pressed={chatOpen}
            onClick={() => {
              // 窗口过窄时三栏放不下,面板会被自动收起;直接静默忽略会让
              // 按钮看起来失灵,这里给出明确原因。
              if (isChatAutoCollapsed) {
                toast.info(t('窗口宽度不足,请拉宽窗口后再打开 AI 助手'));
                return;
              }
              setChatOpen((open) => !open);
            }}
          >
            <MessageSquare className="size-4" />
            {t('AI 助手')}
          </Button>
        ) : null}
      </div>
      <div className="flex min-h-0 flex-1">
        <main
          ref={splitContainerRef}
          className={`grid min-h-0 min-w-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[var(--review-columns)] ${
            isDragging || isChatDragging ? 'select-none' : ''
          }`}
          style={
            {
              // 批改模式下助手占满中间栏原位置:两栏(左预览右助手),
              // 宽度沿用 leftPercent 分割;其余情况维持原两/三栏布局。
              '--review-columns': gradingMode
                ? `calc(${leftPercent}% - 4.5px) 9px minmax(0, 1fr)`
                : showChat
                ? `calc(${leftPercent}% - 6px) 9px minmax(320px, 1fr) 9px calc(${chatPercent}% - 6px)`
                : `calc(${leftPercent}% - 4.5px) 9px minmax(0, 1fr)`,
            } as CSSProperties
          }
        >
        {/* Left: PDF preview */}
        <section className="flex min-h-0 min-w-0 flex-col border-b border-slate-300/80 bg-slate-200/55 lg:border-b-0">
          {data.code_files?.length > 0 && (
            <div className="flex gap-1 border-b border-slate-300/80 bg-white px-5 pt-3">
              <Button size="sm" variant={evidenceTab === 'report' ? 'secondary' : 'ghost'} onClick={() => setEvidenceTab('report')}>
                {t('报告')}
              </Button>
              <Button size="sm" variant={evidenceTab === 'code' ? 'secondary' : 'ghost'} onClick={() => setEvidenceTab('code')}>
                {t('代码证据')}
              </Button>
            </div>
          )}
          <div className="min-h-0 flex-1 p-5">
            {evidenceTab === 'report' ? (
              <PdfViewer
                submissionId={data.id}
                filename={data.original_filename}
                status={data.status}
                className="h-full"
              />
            ) : (
              <CodeEvidencePanel data={data} />
            )}
          </div>
        </section>

        <div
          role="separator"
          aria-label={t('调整作业与评分面板宽度')}
          aria-orientation="vertical"
          aria-valuemin={25}
          aria-valuemax={65}
          aria-valuenow={Math.round(leftPercent)}
          tabIndex={0}
          onPointerDown={handleDividerPointerDown}
          onPointerMove={handleDividerPointerMove}
          onPointerUp={stopDragging}
          onPointerCancel={stopDragging}
          onKeyDown={handleDividerKeyDown}
          className={`group relative z-20 hidden cursor-col-resize touch-none bg-slate-200 outline-none transition-colors lg:block ${
            isDragging ? 'bg-primary/15' : 'hover:bg-primary/10'
          } focus-visible:bg-primary/15`}
        >
          <span
            className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-all ${
              isDragging
                ? 'w-0.5 bg-primary'
                : 'bg-slate-300 group-hover:w-0.5 group-hover:bg-primary/70 group-focus-visible:w-0.5 group-focus-visible:bg-primary'
            }`}
          />
        </div>

        {/* Middle: grading assistant in grading mode / manual review panel otherwise */}
        <section className="flex min-h-0 min-w-0 flex-col bg-slate-50/90">
          {gradingMode || embeddedChat ? (
            <AcpChatPanel
              submissionId={data.id}
              canChat={canChat}
              prefill={assistantPrefill}
              onCollapse={() => setChatOpen(false)}
            />
          ) : isFailed ? (
            <FailedRecoveryPanel data={data} />
          ) : data.status === 'awaiting_mcp' ? (
            <AcpMarkingPanel
              submissionId={data.id}
              questionName={data.question_name}
              questionId={data.question_id ?? undefined}
              onStartInAssistant={startInAssistant}
            />
          ) : (
            <>
              <McpAuditBanner data={data} />
              <AssessmentReviewCard data={data} />
              <ManualReviewPanel
                suggestion={suggestion}
                isReadOnly={isReadOnly}
                reviewerName={reviewerName}
                onFinalize={handleFinalize}
                isFinalizing={finalizeMutation.isPending}
                className="min-h-0 flex-1"
              />
            </>
          )}
        </section>

        {/* 批改模式下助手占中间栏,右侧不再重复渲染助手 */}
        {showChat && !gradingMode ? (
          <>
            <div
              role="separator"
              aria-label={t('调整评分面板与 AI 助手宽度')}
              aria-orientation="vertical"
              aria-valuemin={18}
              aria-valuemax={35}
              aria-valuenow={Math.round(chatPercent)}
              tabIndex={0}
              onPointerDown={handleChatDividerPointerDown}
              onPointerMove={handleChatDividerPointerMove}
              onPointerUp={stopChatDragging}
              onPointerCancel={stopChatDragging}
              onKeyDown={handleChatDividerKeyDown}
              className={`group relative z-20 hidden cursor-col-resize touch-none bg-slate-200 outline-none transition-colors lg:block ${
                isChatDragging ? 'bg-primary/15' : 'hover:bg-primary/10'
              } focus-visible:bg-primary/15`}
            >
              <span
                className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-all ${
                  isChatDragging
                    ? 'w-0.5 bg-primary'
                    : 'bg-slate-300 group-hover:w-0.5 group-hover:bg-primary/70 group-focus-visible:w-0.5 group-focus-visible:bg-primary'
                }`}
              />
            </div>

            {/* Right: AI assistant chat */}
            <aside className="hidden min-h-0 min-w-0 flex-col lg:flex">
              <AcpChatPanel
                submissionId={data.id}
                canChat={canChat}
                prefill={assistantPrefill}
                onCollapse={() => setChatOpen(false)}
              />
            </aside>
          </>
        ) : null}
      </main>
      </div>
    </div>
  );
}

export default function ReviewPage() {
  const { t } = useLanguage();
  const { id } = useParams<{ id: string }>();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId) ? rawNumericId : undefined;

  // awaiting_mcp 已有恢复页所需详情，但仍保持状态轮询直到 MCP 客户端保存建议。
  const {
    data: statusData,
    isError: isStatusError,
    error: statusError,
  } = useSubmissionStatus(numericId);
  const detailEnabled = statusData ? canLoadSubmissionDetail(statusData.status) : false;
  const {
    data,
    isLoading: isDetailLoading,
    isError: isDetailError,
    error: detailError,
  } = useSubmission(
    numericId,
    detailEnabled,
    statusData?.status,
  );

  if (numericId === undefined) {
    return (
      <div className="mx-auto max-w-2xl">
        <BackToHistoryButton className="mb-4" />
        <Card className="elevated-card border-0">
          <CardContent className="flex flex-col items-center gap-4 py-16">
            <div className="flex size-14 items-center justify-center rounded-full bg-muted">
              <AlertCircle className="size-7 text-muted-foreground" />
            </div>
            <p className="text-muted-foreground">{t('无效的记录 ID')}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // status 拉取失败:显示错误而不是永久 loading
  if (isStatusError) {
    return (
      <div className="mx-auto max-w-2xl py-12">
        <BackToHistoryButton className="mb-4" />
        <Alert variant="destructive" className="border-0 shadow-lg">
          <AlertCircle />
          <AlertTitle>{t('加载记录失败')}</AlertTitle>
          <AlertDescription>{statusError?.message}</AlertDescription>
        </Alert>
      </div>
    );
  }

  // status 首次拉取中
  if (!statusData) {
    return (
      <PageLoading />
    );
  }

  // 处理中：展示状态文字 + 上传时间 + 返回按钮
  if (isProcessing(statusData.status) && statusData.status !== 'awaiting_mcp') {
    return (
      <div className="mx-auto max-w-2xl">
        <BackToHistoryButton className="mb-4" />
        <div className="flex flex-col items-center justify-center gap-4 py-32">
          <Loader2 className="size-10 animate-spin text-primary" />
          <div className="text-center">
            <p className="text-base font-medium text-foreground">
              {t(STATUS_TEXT[statusData.status] ?? statusData.status)}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              {t('上传时间：')}
              {dayjs(statusData.uploaded_at).format('YYYY-MM-DD HH:mm:ss')}
            </p>
          </div>
        </div>
      </div>
    );
  }

  // 详情拉取失败:显示错误而不是永久 loading
  if (isDetailError) {
    return (
      <div className="mx-auto max-w-2xl py-12">
        <BackToHistoryButton className="mb-4" />
        <Alert variant="destructive" className="border-0 shadow-lg">
          <AlertCircle />
          <AlertTitle>{t('加载详情失败')}</AlertTitle>
          <AlertDescription>{detailError?.message}</AlertDescription>
        </Alert>
      </div>
    );
  }

  // 终态：详情拉取中
  if (isDetailLoading || !data) {
    return (
      <div className="flex h-svh flex-col">
        <div className="flex flex-1 flex-col overflow-hidden bg-slate-100/80 lg:flex-row">
          <div className="flex min-h-0 w-full flex-col bg-slate-200/55 p-5 lg:w-[55%] lg:border-r">
            <PdfViewerPlaceholder />
          </div>
          <div className="flex min-h-0 w-full items-center justify-center bg-slate-50/90 lg:w-[45%]">
            <Loader2 className="size-8 animate-spin text-primary" />
          </div>
        </div>
      </div>
    );
  }

  // 终态（ready_for_review / reviewed / failed）：展示人工复核布局
  return <ReviewContent data={data} />;
}
