import { Loader2, AlertCircle, FileText, ChevronLeft, CheckCircle2 } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  useSubmission,
  isProcessing,
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

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
      <div className="flex size-10 items-center justify-center rounded-full bg-muted">
        <FileText className="size-5" />
      </div>
      <span className="text-sm">{text}</span>
    </div>
  );
}

export default function ResultPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId)
      ? rawNumericId
      : undefined;

  const { data, isLoading } = useSubmission(numericId);

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

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">加载中...</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="mx-auto max-w-2xl">
        <Card className="elevated-card border-0">
          <CardContent>
            <Empty text="未找到记录" />
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isProcessing(data.status)) {
    return (
      <div className="mx-auto flex max-w-xl flex-col items-center gap-4 py-28 text-center">
        <div className="relative flex size-16 items-center justify-center rounded-2xl bg-primary/10">
          <Loader2 className="size-8 animate-spin text-primary" />
          <span className="absolute inline-flex size-full animate-ping rounded-2xl bg-primary/20" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-foreground">正在批改中</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            当前状态：{data.status}
          </p>
          <p className="text-xs text-muted-foreground">
            上传于 {dayjs(data.uploaded_at).format('YYYY-MM-DD HH:mm:ss')}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => navigate('/history')}>
          <ChevronLeft className="size-4" />
          返回历史记录
        </Button>
      </div>
    );
  }

  if (data.status === 'failed') {
    return (
      <div className="mx-auto max-w-2xl py-12">
        <Alert variant="destructive" className="border-0 shadow-lg">
          <AlertCircle />
          <AlertTitle>批改失败</AlertTitle>
          <AlertDescription>
            {data.error_message || '批改过程中发生未知错误'}
          </AlertDescription>
        </Alert>
        <div className="mt-6 flex justify-center">
          <Button onClick={() => navigate('/')}>
            返回上传
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
          已完成
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
          </div>
          <div className="relative shrink-0 rounded-2xl border border-primary/10 bg-primary/[0.04] px-6 py-4 text-center">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              总分
            </p>
            <p className="text-4xl font-extrabold tracking-tight text-primary">
              {data.score ?? 0}
            </p>
          </div>
        </CardContent>
      </Card>

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
                    </span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                    {item.comment}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <Empty text="无详细评分" />
          )}
        </CardContent>
      </Card>

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
    </div>
  );
}
