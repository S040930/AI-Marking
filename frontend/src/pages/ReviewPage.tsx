import {
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import axios from 'axios';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import dayjs from 'dayjs';
import { Loader2, AlertCircle, FileUp, RotateCcw, Copy, Check } from 'lucide-react';
import {
  useSubmission,
  useSubmissionStatus,
  useFinalizeSubmission,
  useRetrySubmission,
  canLoadSubmissionDetail,
  isProcessing,
  type SubmissionDetail,
  type AiSuggestion,
  type FinalizePayload,
} from '@/api/submissions';
import { Button } from '@/components/ui/button';
import { DOCUMENT_INPUT_ACCEPT } from '@/lib/documentUpload';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { PdfViewer, PdfViewerPlaceholder } from '@/components/PdfViewer';
import ManualReviewPanel from '@/components/ManualReviewPanel';

const STATUS_LABEL: Record<string, string> = {
  pending: '排队等待处理',
  ocr_processing: 'OCR 识别中',
  ocr_done: 'OCR 已完成',
  awaiting_mcp: '等待 MCP 评分',
  ready_for_review: '待审阅',
  reviewed: '已审阅',
  failed: '失败',
};

export function McpWaitingPanel({ submissionId }: { submissionId: number }) {
  const [copied, setCopied] = useState(false);
  const prompt = `请继续使用 AI-Marking 批改作业 #${submissionId}。`;

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
      toast.success('编程助手指令已复制');
    } catch {
      toast.error('复制失败，请手动选择指令');
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-lg rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-wider text-primary">
          作业 #{submissionId} · 等待 MCP 评分
        </p>
        <h2 className="mt-2 text-xl font-semibold">等待编程助手评分</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          这条作业的 OCR 已完成，正等待 MCP 客户端（如 Codex）评分。如果原任务已关闭，可复制下面的恢复指令继续处理，或使用待办列表工具发现作业。
        </p>
        <div className="mt-5 rounded-xl bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">
          {prompt}
        </div>
        <Button className="mt-4" onClick={copyPrompt}>
          {copied ? <Check /> : <Copy />}
          {copied ? '已复制' : '复制编程助手指令'}
        </Button>
        <p className="mt-4 text-xs text-muted-foreground">
          编程助手只会保存评分建议；最终成绩仍需教师回到此网页确认。
        </p>
      </div>
    </div>
  );
}

const CLIENT_LABELS: Record<string, string> = {
  codex: 'Codex',
  'claude-code': 'Claude Code',
  opencode: 'Opencode',
};

function McpAuditBanner({ data }: { data: SubmissionDetail }) {
  const metadata = data.assessment_suggestion?.mcp_metadata;
  const client = metadata?.client;
  const clientLabel = client ? CLIENT_LABELS[client] ?? client : null;
  const rubricSource = metadata?.rubric_source;
  const rubricSourceLabel =
    rubricSource === 'question_extracted'
      ? '题目提取'
      : rubricSource === 'configured'
        ? '配置项'
        : rubricSource === 'built_in_default'
          ? '内置默认'
          : null;
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-primary/10 bg-primary/[0.03] px-5 py-3 text-xs">
      <Badge variant="secondary">{clientLabel ?? 'MCP 客户端'}</Badge>
      <span className="text-muted-foreground">revision {data.grading_revision}</span>
      {rubricSourceLabel && (
        <Badge variant="outline" className="text-muted-foreground">
          rubric: {rubricSourceLabel}
        </Badge>
      )}
    </div>
  );
}

function CodeEvidencePanel({ data }: { data: SubmissionDetail }) {
  if (!data.code_files?.length) return null;
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-5">
      <div className="mb-4 rounded-xl border border-primary/15 bg-white p-4">
        <p className="text-sm font-semibold">提交代码</p>
        <p className="mt-1 text-xs text-muted-foreground">
          新作业由编程助手在当前任务中运行和核验；后端只保存源码与 SHA-256。
        </p>
      </div>
      <div className="space-y-4">
        {data.code_files.map((codeFile) => {
          return (
            <div key={codeFile.id} className="rounded-xl border bg-white p-4 shadow-sm">
              <p className="text-sm font-semibold">第 {codeFile.question_number} 题 · {codeFile.original_filename}</p>
              <p className="mt-1 text-xs text-muted-foreground">SHA-256：{codeFile.source_sha256}</p>
              <details className="mt-3 rounded-lg border bg-slate-50">
                <summary className="cursor-pointer px-3 py-2 text-xs font-medium">查看提交源代码</summary>
                <pre className="max-h-80 overflow-auto border-t p-3 text-xs leading-relaxed">
                  {codeFile.source_text || '源代码不可用'}
                </pre>
              </details>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FailedRecoveryPanel({ data }: { data: SubmissionDetail }) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const retryMutation = useRetrySubmission(data.id);

  const retry = (file?: File) => {
    retryMutation.mutate(file, {
      onSuccess: () => toast.success('已重新进入批改队列'),
      onError: (error) => {
        const detail = axios.isAxiosError(error)
          ? error.response?.data?.detail
          : null;
        toast.error(
          typeof detail === 'string'
            ? detail
            : error.message || '重新批改失败',
        );
      },
    });
  };

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-md rounded-2xl border border-destructive/20 bg-white p-6 shadow-sm">
        <AlertCircle className="mb-4 size-8 text-destructive" />
        <h2 className="text-lg font-semibold">本次批改失败</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {data.error_message || '批改过程中发生未知错误'}
        </p>
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          可以使用原文件重新批改；如果文件内容有问题，请重新选择 PDF 学生作业。
        </p>
        <input
          ref={uploadRef}
          type="file"
          accept={DOCUMENT_INPUT_ACCEPT}
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) retry(file);
            event.target.value = '';
          }}
        />
        <div className="mt-5 flex flex-wrap gap-3">
          <Button
            disabled={retryMutation.isPending}
            onClick={() => retry()}
          >
            {retryMutation.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <RotateCcw />
            )}
            使用原文件重试
          </Button>
          <Button
            variant="outline"
            disabled={retryMutation.isPending}
            onClick={() => uploadRef.current?.click()}
          >
            <FileUp />
            重新上传作业
          </Button>
        </div>
      </div>
    </div>
  );
}

function normalizeSuggestion(data: SubmissionDetail): AiSuggestion | null {
  const raw = data.assessment_suggestion;
  if (!raw) return null;
  return {
    score: raw.score ?? 0,
    max_score: raw.max_score ?? 0,
    confidence: raw.confidence ?? 0,
    feedback: raw.feedback ?? '',
    details: (raw.details ?? []).map((d) => ({
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
          {data.code_files?.length > 0 && (
            <div className="flex gap-1 border-b border-slate-300/80 bg-white px-5 pt-3">
              <Button size="sm" variant={evidenceTab === 'report' ? 'secondary' : 'ghost'} onClick={() => setEvidenceTab('report')}>
                报告
              </Button>
              <Button size="sm" variant={evidenceTab === 'code' ? 'secondary' : 'ghost'} onClick={() => setEvidenceTab('code')}>
                代码证据
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

        {/* Right: manual review panel */}
        <section className="flex min-h-0 min-w-0 flex-col bg-slate-50/90">
          {isFailed ? (
            <FailedRecoveryPanel data={data} />
          ) : data.status === 'awaiting_mcp' ? (
            <McpWaitingPanel submissionId={data.id} />
          ) : (
            <>
              <McpAuditBanner data={data} />
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
  if (isProcessing(statusData.status) && statusData.status !== 'awaiting_mcp') {
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

  // 终态（ready_for_review / reviewed / failed）：展示人工复核布局
  return <ReviewContent data={data} />;
}
