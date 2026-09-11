import {
  AlertCircle,
  FileText,
  CheckCircle2,
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
import { useLanguage } from '@/i18n';
import { PageHeader } from '@/components/common/PageHeader';
import { PageLoading } from '@/components/common/PageLoading';
import { BackToHistoryButton } from '@/components/common/BackToHistoryButton';

export default function ResultPage() {
  const { t } = useLanguage();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId)
      ? rawNumericId
      : undefined;

  // P2-L3:轻量 status 先拉,处理中只靠 status 轮询;终态后再 enable 完整详情。
  // 避免处理中阶段拉取 ocr_text/assessment_suggestion 等大字段(此时均为 null,属浪费)。
  const {
    data: statusData,
    isError: isStatusError,
    error: statusError,
  } = useSubmissionStatus(numericId);
  const detailEnabled = statusData ? isTerminal(statusData.status) : false;
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

  // 处理中:重定向到协同评分页
  if (isProcessing(statusData.status)) {
    return <Navigate to={`/review/${numericId}`} replace />;
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

  // 终态:详情拉取中
  if (isDetailLoading || !data) {
    return (
      <PageLoading text={t('加载结果中...')} />
    );
  }

  // 非已审阅/失败状态(如 ready_for_review):重定向到协同评分页
  if (data.status !== 'reviewed' && data.status !== 'failed') {
    return <Navigate to={`/review/${numericId}`} replace />;
  }

  if (data.status === 'failed') {
    return (
      <div className="mx-auto max-w-2xl py-12">
        <BackToHistoryButton className="mb-4" />
        <Alert variant="destructive" className="border-0 shadow-lg">
          <AlertCircle />
          <AlertTitle>{t('批改失败')}</AlertTitle>
          <AlertDescription>
            {data.error_message || t('批改过程中发生未知错误')}
            {t('。你可以使用原文件重试，或让编程助手重新提交学生作业。')}
          </AlertDescription>
        </Alert>
        <div className="mt-6 flex justify-center">
          <Button onClick={() => navigate(`/review/${data.id}`)}>
            {t('重新批改此记录')}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <BackToHistoryButton />
      <PageHeader
        className="mb-6 items-center"
        title={t('批改结果')}
        description={t('查看 AI 生成的评分、反馈与 OCR 原文')}
        actions={
          <Badge
            variant="outline"
            className="gap-1.5 border-success/20 bg-success/10 text-success"
          >
            <CheckCircle2 className="size-3.5" />
            {t('已审阅')}
          </Badge>
        }
      />

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
              {t('上传时间：')}{dayjs(data.uploaded_at).format('YYYY-MM-DD HH:mm:ss')}
              {data.completed_at && (
                <>
                  {' '}
                  · {t('完成时间：')}
                  {dayjs(data.completed_at).format('YYYY-MM-DD HH:mm:ss')}
                </>
              )}
            </p>
            {data.question_original_filename && (
              <p className="mt-1 text-sm text-muted-foreground">
                {t('作业题目：')}{data.question_original_filename}
              </p>
            )}
          </div>
          <div className="relative shrink-0 rounded-2xl border border-primary/10 bg-primary/[0.04] px-6 py-4 text-center">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('总分')}
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

      {data.assessment_suggestion?.mcp_metadata && (
        <Card className="elevated-card overflow-hidden">
          <CardHeader className="pb-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="size-5 text-primary" />
              {t('MCP 评分来源')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
              <span className="text-muted-foreground">
                revision <strong>{data.grading_revision}</strong>
              </span>
              {data.confidence !== null && (
                <span className="text-muted-foreground">
                  {t('置信度：')}
                  <strong>{Math.round(data.confidence * 100)}%</strong>
                </span>
              )}
            </div>
            {data.assessment_suggestion.mcp_metadata.client && (
              <p className="text-muted-foreground">
                {t('评分客户端：')}{data.assessment_suggestion.mcp_metadata.client}
                {data.assessment_suggestion.mcp_metadata.generated_at
                  ? ` · ${dayjs(data.assessment_suggestion.mcp_metadata.generated_at).format('YYYY-MM-DD HH:mm:ss')}`
                  : ''}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {data.reviewed_by && (
        <Alert>
          <ShieldCheck />
          <AlertTitle>{t('已审阅')}</AlertTitle>
          <AlertDescription>
            {t('审核教师：')}{data.reviewed_by}
            {data.completed_at && ` · ${t('完成于')}${dayjs(data.completed_at).format('YYYY-MM-DD HH:mm:ss')}`}
          </AlertDescription>
        </Alert>
      )}

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-base">{t('总体反馈')}</CardTitle>
        </CardHeader>
        <CardContent>
          {data.feedback ? (
            <div className="rounded-xl border border-border bg-muted/50 p-4">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                {data.feedback}
              </p>
            </div>
          ) : (
            <Empty text={t('无总体反馈')} />
          )}
        </CardContent>
      </Card>

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-base">{t('详细评分项')}</CardTitle>
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
                      {t('证据：')}{item.evidence.join('；')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty text={t('无详细评分')} />
          )}
        </CardContent>
      </Card>

      {data.question_ocr_text && (
        <Card className="elevated-card overflow-hidden">
          <CardHeader className="pb-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <FileText className="size-5 text-primary/60" />
              {t('作业题目')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Accordion type="single" collapsible>
              <AccordionItem value="question" className="border-b-0">
                <AccordionTrigger className="rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm font-medium hover:bg-muted hover:no-underline">
                  {t('展开/折叠作业题目原文')}
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
          <CardTitle className="text-base">{t('OCR 原文')}</CardTitle>
        </CardHeader>
        <CardContent>
          <Accordion type="single" collapsible>
            <AccordionItem value="ocr" className="border-b-0">
              <AccordionTrigger className="rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm font-medium hover:bg-muted hover:no-underline">
                {t('展开/折叠 OCR 识别原文')}
              </AccordionTrigger>
              <AccordionContent>
                {data.ocr_text ? (
                  <div className="mt-2 max-h-96 overflow-auto rounded-xl border border-border bg-muted/50 p-4">
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                      {data.ocr_text}
                    </p>
                  </div>
                ) : (
                  <Empty text={t('无 OCR 文本')} />
                )}
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </CardContent>
      </Card>

      <div className="flex justify-center gap-3 pt-4">
        <Button variant="outline" onClick={() => navigate(`/review/${data.id}`)}>
          {t('查看评分工作台')}
        </Button>
      </div>
    </div>
  );
}
