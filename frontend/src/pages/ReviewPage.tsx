import {
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import dayjs from 'dayjs';
import { Loader2, AlertCircle, ChevronLeft } from 'lucide-react';
import {
  useSubmission,
  useSubmissionStatus,
  useFinalizeSubmission,
  canLoadSubmissionDetail,
  isProcessing,
  type SubmissionDetail,
  type FinalizePayload,
} from '@/api/submissions';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { PdfViewer, PdfViewerPlaceholder } from '@/components/PdfViewer';
import ManualReviewPanel from '@/components/ManualReviewPanel';
import { McpWaitingPanel } from '@/components/review/McpWaitingPanel';
import { McpAuditBanner } from '@/components/review/McpAuditBanner';
import { AssessmentReviewCard } from '@/components/review/AssessmentReviewCard';
import { CodeEvidencePanel } from '@/components/review/CodeEvidencePanel';
import { FailedRecoveryPanel } from '@/components/review/FailedRecoveryPanel';
import { normalizeSuggestion } from '@/components/review/normalizeSuggestion';
import { useLanguage } from '@/i18n';

const STATUS_LABEL: Record<string, string> = {
  pending: '排队等待处理',
  ocr_processing: 'OCR 识别中',
  ocr_done: 'OCR 已完成',
  awaiting_mcp: '等待 MCP 评分',
  ready_for_review: '待审阅',
  reviewed: '已审阅',
  failed: '失败',
};

function ReviewContent({ data }: { data: SubmissionDetail }) {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const splitContainerRef = useRef<HTMLElement>(null);
  const draggingRef = useRef(false);
  const [leftPercent, setLeftPercent] = useState(55);
  const [evidenceTab, setEvidenceTab] = useState<'report' | 'code'>('report');
  const [isDragging, setIsDragging] = useState(false);
  const isReadOnly = data.status === 'reviewed';
  const isFailed = data.status === 'failed';

  const reviewerName = 'Teacher';

  const suggestion = normalizeSuggestion(data);
  const finalizeMutation = useFinalizeSubmission(data.id);

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
    setLeftPercent(Math.min(70, Math.max(35, nextPercent)));
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

  const handleDividerKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const delta = event.key === 'ArrowLeft' ? -2 : 2;
    setLeftPercent((current) => Math.min(70, Math.max(35, current + delta)));
  };

  return (
    <div className="-m-8 flex h-[calc(100dvh-4rem)] flex-col overflow-hidden bg-slate-100/80">
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-slate-300/80 bg-white px-4">
        <Button
          variant="ghost"
          onClick={() => navigate('/history')}
          className="-ml-2 text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft />
          {t('返回历史')}
        </Button>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          {data.original_filename}
        </span>
      </div>
      <main
        ref={splitContainerRef}
        className={`grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[var(--review-columns)] ${
          isDragging ? 'select-none' : ''
        }`}
        style={
          {
            '--review-columns': `calc(${leftPercent}% - 4.5px) 9px minmax(0, 1fr)`,
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
          aria-valuemin={35}
          aria-valuemax={70}
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

        {/* Right: manual review panel */}
        <section className="flex min-h-0 min-w-0 flex-col bg-slate-50/90">
          {isFailed ? (
            <FailedRecoveryPanel data={data} />
          ) : data.status === 'awaiting_mcp' ? (
            <McpWaitingPanel submissionId={data.id} />
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
      </main>
    </div>
  );
}

export default function ReviewPage() {
  const { t } = useLanguage();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId) ? rawNumericId : undefined;

  // awaiting_mcp 已有恢复页所需详情，但仍保持状态轮询直到 MCP 客户端保存建议。
  const { data: statusData } = useSubmissionStatus(numericId);
  const detailEnabled = statusData ? canLoadSubmissionDetail(statusData.status) : false;
  const { data, isLoading: isDetailLoading } = useSubmission(
    numericId,
    detailEnabled,
    statusData?.status,
  );

  if (numericId === undefined) {
    return (
      <div className="mx-auto max-w-2xl">
        <Button
          variant="ghost"
          onClick={() => navigate('/history')}
          className="-ml-2 mb-4 text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft />
          {t('返回历史')}
        </Button>
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

  // status 首次拉取中
  if (!statusData) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">{t('加载中...')}</p>
      </div>
    );
  }

  // 处理中：展示状态文字 + 上传时间 + 返回按钮
  if (isProcessing(statusData.status) && statusData.status !== 'awaiting_mcp') {
    return (
      <div className="mx-auto max-w-2xl">
        <Button
          variant="ghost"
          onClick={() => navigate('/history')}
          className="-ml-2 mb-4 text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft />
          {t('返回历史')}
        </Button>
        <div className="flex flex-col items-center justify-center gap-4 py-32">
          <Loader2 className="size-10 animate-spin text-primary" />
          <div className="text-center">
            <p className="text-base font-medium text-foreground">
              {t(STATUS_LABEL[statusData.status] ?? statusData.status)}
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

  // 终态：详情拉取中
  if (isDetailLoading || !data) {
    return (
      <div className="-m-8 flex h-[calc(100dvh-4rem)] flex-col">
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
