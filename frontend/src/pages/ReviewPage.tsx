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
import { Loader2, AlertCircle } from 'lucide-react';
import {
  useSubmission,
  useSubmissionStatus,
  useFinalizeSubmission,
  isProcessing,
  isTerminal,
  type SubmissionDetail,
  type AiSuggestion,
  type FinalizePayload,
} from '@/api/submissions';
import { useConfig } from '@/api/config';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { PdfViewer, PdfViewerPlaceholder } from '@/components/PdfViewer';
import ReviewChatPanel from '@/components/ReviewChatPanel';

const STATUS_LABEL: Record<string, string> = {
  pending: '排队等待处理',
  ocr_processing: 'OCR 识别中',
  ocr_done: 'OCR 已完成',
  agent_grading: 'AI 评分中',
  agent_reviewing: 'AI 复核中',
  agent_revising: 'AI 修订中',
  ready_for_review: '待审阅',
  reviewed: '已审阅',
  failed: '失败',
};

function normalizeAiSuggestion(data: SubmissionDetail): AiSuggestion | null {
  if (!data.ai_suggestion) return null;
  return {
    score: data.ai_suggestion.score ?? 0,
    max_score: data.ai_suggestion.max_score ?? 0,
    confidence: data.ai_suggestion.confidence ?? 0,
    feedback: data.ai_suggestion.feedback ?? '',
    details: (data.ai_suggestion.details ?? []).map((d) => ({
      criterion: d.criterion,
      score: d.score,
      max_score: d.max_score ?? 0,
      comment: d.comment,
      evidence: d.evidence,
    })),
  };
}

function ReviewContent({ data }: { data: SubmissionDetail }) {
  const navigate = useNavigate();
  const splitContainerRef = useRef<HTMLElement>(null);
  const draggingRef = useRef(false);
  const [leftPercent, setLeftPercent] = useState(55);
  const [isDragging, setIsDragging] = useState(false);
  const isReadOnly = data.status === 'reviewed';
  const isFailed = data.status === 'failed';

  const { data: config } = useConfig();
  const reviewerName = config?.operator_name?.trim() || 'Teacher';

  const aiSuggestion = normalizeAiSuggestion(data);
  const finalizeMutation = useFinalizeSubmission(data.id);

  const handleFinalize = (payload: FinalizePayload) => {
    finalizeMutation.mutate(
      { ...payload, reviewer_name: reviewerName },
      {
        onSuccess: () => {
          toast.success('评分已提交');
          navigate(`/result/${data.id}`);
        },
        onError: () => toast.error('提交失败，请稍后重试'),
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
          <div className="min-h-0 flex-1 p-5">
            <PdfViewer
              submissionId={data.id}
              filename={data.original_filename}
              status={data.status}
              className="h-full"
            />
          </div>
        </section>

        <div
          role="separator"
          aria-label="调整作业与评分面板宽度"
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

        {/* Right: AI chat panel */}
        <section className="flex min-h-0 min-w-0 flex-col bg-slate-50/90">
          <ReviewChatPanel
            submissionId={data.id}
            initialSuggestion={aiSuggestion}
            isReadOnly={isReadOnly || isFailed}
            reviewerName={reviewerName}
            onFinalize={handleFinalize}
            className="flex-1"
          />
        </section>
      </main>
    </div>
  );
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId) ? rawNumericId : undefined;

  // 轻量 status 先拉，处理中只靠 status 轮询；终态后再 enable 完整详情。
  const { data: statusData } = useSubmissionStatus(numericId);
  const detailEnabled = statusData ? isTerminal(statusData.status) : false;
  const { data, isLoading: isDetailLoading } = useSubmission(numericId, detailEnabled);

  if (numericId === undefined) {
    return (
      <div className="mx-auto max-w-2xl">
        <Card className="elevated-card border-0">
          <CardContent className="flex flex-col items-center gap-4 py-16">
            <div className="flex size-14 items-center justify-center rounded-full bg-muted">
              <AlertCircle className="size-7 text-muted-foreground" />
            </div>
            <p className="text-muted-foreground">无效的记录 ID</p>
            <Button variant="outline" onClick={() => navigate('/history')}>
              返回历史记录
            </Button>
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
        <p className="text-sm text-muted-foreground">加载中...</p>
      </div>
    );
  }

  // 处理中：展示状态文字 + 上传时间 + 返回按钮
  if (isProcessing(statusData.status)) {
    return (
      <div className="mx-auto max-w-2xl">
        <div className="flex flex-col items-center justify-center gap-4 py-32">
          <Loader2 className="size-10 animate-spin text-primary" />
          <div className="text-center">
            <p className="text-base font-medium text-foreground">
              {STATUS_LABEL[statusData.status] ?? statusData.status}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              上传时间：
              {dayjs(statusData.uploaded_at).format('YYYY-MM-DD HH:mm:ss')}
            </p>
          </div>
          <Button variant="outline" onClick={() => navigate('/history')}>
            返回历史
          </Button>
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

  // 终态（ready_for_review / reviewed / failed）：展示协同评分布局
  return <ReviewContent data={data} />;
}
