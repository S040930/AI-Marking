import {
  Loader2,
  AlertCircle,
  FileText,
  ChevronLeft,
  CheckCircle2,
  Bot,
  ShieldCheck,
} from 'lucide-react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  useSubmission,
  useSubmissionStatus,
  isProcessing,
  isTerminal,
  type DetailItem,
} from '@/api/submissions';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Empty } from '@/components/Empty';

export default function ResultPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId)
      ? rawNumericId
      : undefined;

  // P2-L3:轻量 status 先拉,处理中只靠 status 轮询;终态后再 enable 完整详情。
  // 避免处理中阶段拉取 ocr_text/ai_result 等大字段(此时均为 null,属浪费)。
  const { data: statusData } = useSubmissionStatus(numericId);
  const detailEnabled = statusData ? isTerminal(statusData.status) : false;
  const { data, isLoading: isDetailLoading } = useSubmission(
    numericId,
    detailEnabled,
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

  // 处理中:重定向到协同评分页
  if (isProcessing(statusData.status)) {
    return <Navigate to={`/review/${numericId}`} replace />;
  }

  // 终态:详情拉取中
  if (isDetailLoading || !data) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">加载结果中...</p>
      </div>
    );
  }

  // 非已审阅/失败状态(如 ready_for_review):重定向到协同评分页
  if (data.status !== 'reviewed' && data.status !== 'failed') {
    return <Navigate to={`/review/${numericId}`} replace />;
  }

  if (data.status === 'failed') {
    return (
      <div className="mx-auto max-w-2xl py-12">
        <Alert variant="destructive" className="border-0 shadow-lg">
          <AlertCircle />
          <AlertTitle>批改失败</AlertTitle>
          <AlertDescription>
            {data.error_message || '批改过程中发生未知错误'}
            。你可以在当前记录上使用原文件重试，或重新上传学生 PDF 后重试。
          </AlertDescription>
        </Alert>
        <div className="mt-6 flex justify-center">
          <Button onClick={() => navigate(`/review/${data.id}`)}>
            重新批改此记录
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            批改结果
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            查看 AI 生成的评分、反馈与 OCR 原文
          </p>
        </div>
        <Badge
          variant="outline"
          className="gap-1.5 border-success/20 bg-success/10 text-success"
        >
          <CheckCircle2 className="size-3.5" />
          已审阅
        </Badge>
      </div>

      <Card className="elevated-card overflow-hidden">
        <CardContent className="flex items-start justify-between gap-6 p-6">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <FileText className="size-5 text-primary/60" />
              <h2 className="truncate text-lg font-semibold text-foreground">
                {data.original_filename}
              </h2>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              上传时间：{dayjs(data.uploaded_at).format('YYYY-MM-DD HH:mm:ss')}
              {data.completed_at && (
                <>
                  {' '}
                  · 完成时间：
                  {dayjs(data.completed_at).format('YYYY-MM-DD HH:mm:ss')}
                </>
              )}
            </p>
            {data.question_original_filename && (
              <p className="mt-1 text-sm text-muted-foreground">
                作业题目：{data.question_original_filename}
              </p>
            )}
          </div>
          <div className="relative shrink-0 rounded-2xl border border-primary/10 bg-primary/[0.04] px-6 py-4 text-center">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              总分
            </p>
            <p className="text-4xl font-extrabold tracking-tight text-primary">
              {data.score ?? '--'}
              {data.max_score ? (
                <span className="text-base font-medium text-muted-foreground">
                  /{data.max_score}
                </span>
              ) : null}
            </p>
          </div>
        </CardContent>
      </Card>

      {data.agent_trace && data.agent_trace.length > 0 && (
        <Card className="elevated-card overflow-hidden">
          <CardHeader className="pb-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <Bot className="size-5 text-primary" />
              Agent 执行摘要
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.confidence !== null && (
              <div className="rounded-lg border bg-muted/40 px-3 py-2 text-sm">
                自动复核置信度：
                <strong className="ml-1">{Math.round(data.confidence * 100)}%</strong>
              </div>
            )}
            {data.agent_trace.map((event, index) => (
              <div key={`${event.node}-${index}`} className="flex gap-3 rounded-xl border p-3">
                <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
                  {index + 1}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold">
                    {event.node} · 第 {event.attempt} 次
                  </p>
                  <p className="mt-1 text-sm text-muted-foreground">{event.summary}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    耗时 {event.duration_ms} ms
                  </p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {data.reviewed_by && (
        <Alert>
          <ShieldCheck />
          <AlertTitle>已审阅</AlertTitle>
          <AlertDescription>
            审核教师：{data.reviewed_by}
            {data.completed_at && ` · 完成于 ${dayjs(data.completed_at).format('YYYY-MM-DD HH:mm:ss')}`}
          </AlertDescription>
        </Alert>
      )}

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-base">总体反馈</CardTitle>
        </CardHeader>
        <CardContent>
          {data.feedback ? (
            <div className="rounded-xl border border-border bg-muted/50 p-4">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                {data.feedback}
              </p>
            </div>
          ) : (
            <Empty text="无总体反馈" />
          )}
        </CardContent>
      </Card>

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-base">详细评分项</CardTitle>
        </CardHeader>
        <CardContent>
          {data.details && data.details.length > 0 ? (
            <div className="flex flex-col divide-y divide-border/60">
              {data.details.map((item: DetailItem, index) => (
                <div
                  key={index}
                  className="py-4 first:pt-0 last:pb-0"
                >
                  <div className="flex items-start justify-between gap-4">
                    <p className="font-semibold text-foreground">{item.criterion}</p>
                    <span className="shrink-0 rounded-lg bg-primary/10 px-2.5 py-0.5 text-sm font-bold text-primary">
                      {item.score}
                      {item.max_score !== undefined ? `/${item.max_score}` : ''}
                    </span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                    {item.comment}
                  </p>
                  {item.evidence && item.evidence.length > 0 && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      证据：{item.evidence.join('；')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty text="无详细评分" />
          )}
        </CardContent>
      </Card>

      {data.question_ocr_text && (
        <Card className="elevated-card overflow-hidden">
          <CardHeader className="pb-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <FileText className="size-5 text-primary/60" />
              作业题目
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Accordion type="single" collapsible>
              <AccordionItem value="question" className="border-b-0">
                <AccordionTrigger className="rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm font-medium hover:bg-muted hover:no-underline">
                  展开/折叠作业题目原文
                </AccordionTrigger>
                <AccordionContent>
                  <div className="mt-2 max-h-96 overflow-auto rounded-xl border border-border bg-muted/50 p-4">
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                      {data.question_ocr_text}
                    </p>
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          </CardContent>
        </Card>
      )}

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-base">OCR 原文</CardTitle>
        </CardHeader>
        <CardContent>
          <Accordion type="single" collapsible>
            <AccordionItem value="ocr" className="border-b-0">
              <AccordionTrigger className="rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm font-medium hover:bg-muted hover:no-underline">
                展开/折叠 OCR 识别原文
              </AccordionTrigger>
              <AccordionContent>
                {data.ocr_text ? (
                  <div className="mt-2 max-h-96 overflow-auto rounded-xl border border-border bg-muted/50 p-4">
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                      {data.ocr_text}
                    </p>
                  </div>
                ) : (
                  <Empty text="无 OCR 文本" />
                )}
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </CardContent>
      </Card>

      <div className="flex justify-center pt-4">
        <Button variant="outline" onClick={() => navigate('/history')}>
          <ChevronLeft className="size-4" />
          返回历史
        </Button>
      </div>
    </div>
  );
}
