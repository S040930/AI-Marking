import {
  useEffect,
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
import { Loader2, AlertCircle, FileUp, RotateCcw, Copy, Check, ShieldCheck } from 'lucide-react';
import {
  useSubmission,
  useSubmissionStatus,
  useFinalizeSubmission,
  useRetrySubmission,
  useReviewSubmission,
  didReviewRun,
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
import ReviewChatPanel from '@/components/ReviewChatPanel';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

const STATUS_LABEL: Record<string, string> = {
  pending: '排队等待处理',
  ocr_processing: 'OCR 识别中',
  ocr_done: 'OCR 已完成',
  agent_grading: 'AI 评分中',
  agent_reviewing: 'AI 复核中',
  agent_revising: 'AI 修订中',
  awaiting_codex: '等待 Codex 评分',
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

export function CodexWaitingPanel({ submissionId }: { submissionId: number }) {
  const [copied, setCopied] = useState(false);
  const prompt = `请继续使用 AI-Marking 批改作业 #${submissionId}。`;

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
      toast.success('Codex 指令已复制');
    } catch {
      toast.error('复制失败，请手动选择指令');
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-lg rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-wider text-primary">
          Codex 作业 #{submissionId}
        </p>
        <h2 className="mt-2 text-xl font-semibold">等待 Codex 继续评分</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          这条作业通常由 Codex 自动等待 OCR 并继续评分。如果原任务已关闭，可复制下面的恢复指令继续处理。
        </p>
        <div className="mt-5 rounded-xl bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">
          {prompt}
        </div>
        <Button className="mt-4" onClick={copyPrompt}>
          {copied ? <Check /> : <Copy />}
          {copied ? '已复制' : '复制 Codex 指令'}
        </Button>
        <p className="mt-4 text-xs text-muted-foreground">
          Codex 只会保存评分建议；最终成绩仍需教师回到此网页确认。
        </p>
      </div>
    </div>
  );
}

function CodexAuditBanner({ data }: { data: SubmissionDetail }) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-primary/10 bg-primary/[0.03] px-5 py-3 text-xs">
      <Badge variant="secondary">Codex</Badge>
      <span className="text-muted-foreground">revision {data.grading_revision}</span>
    </div>
  );
}

function CodeEvidencePanel({ data }: { data: SubmissionDetail }) {
  if (!data.code_files?.length) return null;
  const historical = data.code_files.some(
    (codeFile) => codeFile.execution_result || codeFile.artifacts?.length || codeFile.visual_reviews?.length,
  );
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-5">
      <div className="mb-4 rounded-xl border border-primary/15 bg-white p-4">
        <p className="text-sm font-semibold">提交代码</p>
        <p className="mt-1 text-xs text-muted-foreground">
          新作业由 Codex 在当前任务中运行和核验；后端只保存源码与 SHA-256。
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
              {historical && codeFile.execution_result ? (
                <details className="mt-3 rounded-lg border border-amber-200 bg-amber-50">
                  <summary className="cursor-pointer px-3 py-2 text-xs font-medium">查看历史后端执行记录</summary>
                  <pre className="max-h-48 overflow-auto border-t p-3 text-xs">
                    {JSON.stringify(codeFile.execution_result, null, 2)}
                  </pre>
                </details>
              ) : null}
              {historical && codeFile.artifacts?.length ? (
                <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                  <p>该历史记录包含 {codeFile.artifacts.length} 个旧产物：</p>
                  {codeFile.artifacts.map((artifact) => (
                    <a
                      key={artifact.artifact_id}
                      href={`/api/submissions/${data.id}/code-assets/code:${codeFile.id}:${artifact.artifact_id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="block text-primary underline"
                    >
                      {artifact.filename}
                    </a>
                  ))}
                </div>
              ) : null}
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

function ReviewContent({ data }: { data: SubmissionDetail }) {
  const navigate = useNavigate();
  const splitContainerRef = useRef<HTMLElement>(null);
  const draggingRef = useRef(false);
  const [leftPercent, setLeftPercent] = useState(55);
  const [evidenceTab, setEvidenceTab] = useState<'report' | 'code'>('report');
  const [isDragging, setIsDragging] = useState(false);
  const [reviewPromptOpen, setReviewPromptOpen] = useState(false);
  const isReadOnly = data.status === 'reviewed';
  const isFailed = data.status === 'failed';

  const reviewerName = 'Teacher';

  const aiSuggestion = normalizeAiSuggestion(data);
  const finalizeMutation = useFinalizeSubmission(data.id);
  const reviewMutation = useReviewSubmission(data.id);

  // 评分完成（ready_for_review）且尚未复核时，主动询问是否需要 AI 复核。
  const needsReviewPrompt =
    data.status === 'ready_for_review' && !didReviewRun(data);

  useEffect(() => {
    if (needsReviewPrompt) setReviewPromptOpen(true);
  }, [needsReviewPrompt]);

  const handleRunReview = () => {
    reviewMutation.mutate(undefined, {
      onSuccess: () => {
        toast.success('AI 复核完成，已更新评分建议');
        setReviewPromptOpen(false);
      },
      onError: (error) => {
        const detail = axios.isAxiosError(error)
          ? error.response?.data?.detail
          : null;
        toast.error(
          typeof detail === 'string'
            ? detail
            : error.message || 'AI 复核失败，请稍后重试',
        );
        setReviewPromptOpen(false);
      },
    });
  };

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

        {/* Right: AI chat panel */}
        <section className="flex min-h-0 min-w-0 flex-col bg-slate-50/90">
          {isFailed ? (
            <FailedRecoveryPanel data={data} />
          ) : data.status === 'awaiting_codex' ? (
            <CodexWaitingPanel submissionId={data.id} />
          ) : (
            <>
              {data.grading_mode === 'codex' && <CodexAuditBanner data={data} />}
              <ReviewChatPanel
                submissionId={data.id}
                initialSuggestion={aiSuggestion}
                isReadOnly={isReadOnly}
                reviewerName={reviewerName}
                onFinalize={handleFinalize}
                disableChat={data.grading_mode === 'codex'}
                className="min-h-0 flex-1"
              />
            </>
          )}
        </section>
      </main>

      {/* 评分完成后主动询问是否需要 AI 复核 */}
      <AlertDialog open={reviewPromptOpen} onOpenChange={setReviewPromptOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>是否需要 AI 复核本次评分？</AlertDialogTitle>
            <AlertDialogDescription>
              AI 已为本次作业生成评分建议。复核会由独立的 AI 评审再次检查评分草稿，
              核对评分标准覆盖、分数计算与证据支持，并给出修订建议。复核可能产生额外
              的 Token 消耗，你可以在确认前选择跳过。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={reviewMutation.isPending}>
              暂不复核
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                handleRunReview();
              }}
              disabled={reviewMutation.isPending}
            >
              {reviewMutation.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <ShieldCheck />
              )}
              开始复核
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId) ? rawNumericId : undefined;

  // awaiting_codex 已有恢复页所需详情，但仍保持状态轮询直到 Codex 保存建议。
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
  if (isProcessing(statusData.status) && statusData.status !== 'awaiting_codex') {
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
