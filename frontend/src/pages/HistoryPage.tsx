import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { ChevronLeft, ChevronRight, FileText, History } from 'lucide-react';
import {
  useSubmissions,
  useSubmissionsCount,
  isProcessing,
  type SubmissionOut,
  type SubmissionStatus,
} from '@/api/submissions';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

export const STATUS_TEXT: Record<SubmissionStatus, string> = {
  pending: '待处理',
  ocr_processing: 'OCR识别中',
  ocr_done: 'OCR完成',
  agent_grading: 'Agent评分中',
  agent_reviewing: 'Agent复核中',
  agent_revising: 'Agent修正中',
  ready_for_review: '待审阅',
  reviewed: '已审阅',
  failed: '失败',
};

const PAGE_SIZE = 10;

export function StatusBadge({ status }: { status: SubmissionStatus }) {
  if (status === 'reviewed') {
    return (
      <Badge className="border border-success/20 bg-success/10 text-success hover:bg-success/15">
        {STATUS_TEXT[status]}
      </Badge>
    );
  }
  if (status === 'failed') {
    return <Badge variant="destructive">{STATUS_TEXT[status]}</Badge>;
  }
  if (status === 'ready_for_review') {
    return (
      <Badge className="border border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100">
        {STATUS_TEXT[status]}
      </Badge>
    );
  }
  if (isProcessing(status)) {
    return (
      <Badge
        variant="secondary"
        className="gap-1.5 pr-2.5 text-primary"
      >
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping motion-reduce:animate-none rounded-full bg-primary opacity-75" />
          <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
        </span>
        {STATUS_TEXT[status]}
      </Badge>
    );
  }
  return <Badge variant="outline">{STATUS_TEXT[status]}</Badge>;
}

export default function HistoryPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const { data, isLoading, isPlaceholderData } = useSubmissions({
    page,
    pageSize: PAGE_SIZE,
  });
  // 总数独立拉取,不随列表 3s 轮询(避免每次轮询都算 COUNT)。
  const { data: countData } = useSubmissionsCount();

  const rows = data?.items ?? [];
  const total = countData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            历史记录
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            查看已上传作业的批改状态与评分结果
          </p>
        </div>
        {!isLoading && (
          <span className="text-xs font-medium text-muted-foreground">
            共 {total} 条记录
          </span>
        )}
      </div>

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-lg">批改记录</CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          <div className="relative w-full overflow-x-auto rounded-xl border border-border">
            <Table>
              <TableHeader className="border-b bg-muted/50 text-muted-foreground">
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[40%] font-medium">文件名</TableHead>
                  <TableHead className="w-[120px] font-medium">状态</TableHead>
                  <TableHead className="w-[90px] font-medium">分数</TableHead>
                  <TableHead className="w-[180px] font-medium">上传时间</TableHead>
                  <TableHead className="w-[80px] font-medium">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={`skeleton-${i}`}>
                      <TableCell colSpan={5} className="py-3">
                        <Skeleton className="h-5 w-full" />
                      </TableCell>
                    </TableRow>
                  ))
                ) : rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="h-40 text-center">
                      <div className="animate-fade-in-up motion-reduce:animate-none relative flex flex-col items-center gap-3 text-muted-foreground">
                        <div className="flex size-12 items-center justify-center rounded-full bg-muted">
                          <History className="size-6" />
                        </div>
                        <span className="text-sm">暂无批改记录</span>
                        <Button
                          size="sm"
                          onClick={() => navigate('/')}
                        >
                          去上传
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : (
                  rows.map((item: SubmissionOut) => {
                    // 已审阅跳结果页,未审阅/失败跳协同评分页(允许手动评分)
                    const targetPath =
                      item.status === 'reviewed'
                        ? `/result/${item.id}`
                        : `/review/${item.id}`;
                    return (
                    <TableRow
                      key={item.id}
                      className="group cursor-pointer transition-colors hover:bg-muted/40"
                      onClick={() => navigate(targetPath)}
                    >
                      <TableCell className="max-w-0">
                        <TooltipProvider delayDuration={300}>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <div className="flex items-center gap-2">
                                <FileText className="size-4 shrink-0 text-primary/60" />
                                <span className="truncate text-sm font-medium text-foreground">
                                  {item.original_filename}
                                </span>
                              </div>
                            </TooltipTrigger>
                            <TooltipContent side="top" align="start">
                              <p className="max-w-xs break-all text-xs">
                                {item.original_filename}
                              </p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={item.status} />
                      </TableCell>
                      <TableCell>
                        {item.score === null ? (
                          <span className="text-muted-foreground">-</span>
                        ) : (
                          <span className="font-semibold text-foreground">
                            {item.score}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {dayjs(item.uploaded_at).format('YYYY-MM-DD HH:mm:ss')}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-auto p-0 font-semibold text-primary hover:text-primary/80 hover:underline"
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(targetPath);
                          }}
                        >
                          查看
                        </Button>
                      </TableCell>
                    </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>

          {!isLoading && total > PAGE_SIZE && (
            <div className="flex items-center justify-end gap-2 pt-5">
              <Button
                variant="outline"
                size="sm"
                disabled={currentPage <= 1 || isPlaceholderData}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <ChevronLeft className="size-4" />
                上一页
              </Button>
              <span className="text-sm text-muted-foreground">
                第 <span className="font-semibold text-foreground">{currentPage}</span> / {totalPages} 页
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={currentPage >= totalPages || isPlaceholderData}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                下一页
                <ChevronRight className="size-4" />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
