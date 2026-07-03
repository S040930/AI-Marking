import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';
import { toast } from 'sonner';
import { ChevronLeft, ChevronRight, FileText, History, Trash2 } from 'lucide-react';
import {
  useSubmissions,
  useSubmissionsCount,
  useBatchDeleteSubmissions,
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
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
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

export function isDeletableStatus(status: SubmissionStatus): boolean {
  return (
    status === 'ready_for_review' ||
    status === 'reviewed' ||
    status === 'failed'
  );
}

export function shouldMoveToPreviousPage(
  page: number,
  rowCount: number,
  deletedCount: number,
): boolean {
  return page > 1 && rowCount > 0 && rowCount <= deletedCount;
}

function resolveDeleteError(error: unknown): {
  message: string;
  isConflict: boolean;
} {
  if (axios.isAxiosError(error) && error.response?.status === 409) {
    const detail = error.response.data?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return { message: String(detail.message), isConflict: true };
    }
    return {
      message: '正在处理的记录不可删除，请等待批改完成后重试',
      isConflict: true,
    };
  }
  return { message: '删除失败，请稍后重试', isConflict: false };
}

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
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const {
    data,
    isLoading,
    isPlaceholderData,
    refetch: refetchSubmissions,
  } = useSubmissions({
    page,
    pageSize: PAGE_SIZE,
  });
  // 总数独立拉取,不随列表 3s 轮询(避免每次轮询都算 COUNT)。
  const { data: countData, refetch: refetchCount } = useSubmissionsCount();
  const deleteMutation = useBatchDeleteSubmissions();

  const rows = data?.items ?? [];
  const total = countData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);

  // 翻页时清空选中,避免跨页选中造成困惑
  useEffect(() => {
    setSelectedIds(new Set());
  }, [page]);

  const rowIds = rows
    .filter((row) => isDeletableStatus(row.status))
    .map((row) => row.id);
  const allOnPageSelected =
    rowIds.length > 0 && rowIds.every((id) => selectedIds.has(id));
  const someOnPageSelected = rowIds.some((id) => selectedIds.has(id));

  const toggleRow = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) {
        rowIds.forEach((id) => next.delete(id));
      } else {
        rowIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const handleConfirmDelete = () => {
    const ids = Array.from(selectedIds);
    deleteMutation.mutate(ids, {
      onSuccess: (res) => {
        toast.success(`已删除 ${res.deleted_count} 条记录`);
        setSelectedIds(new Set());
        setDeleteDialogOpen(false);
        if (shouldMoveToPreviousPage(page, rows.length, res.deleted_count)) {
          setPage((current) => Math.max(1, current - 1));
        }
      },
      onError: (error) => {
        const result = resolveDeleteError(error);
        toast.error(result.message);
        if (result.isConflict) {
          setDeleteDialogOpen(false);
          void refetchSubmissions();
          void refetchCount();
        }
      },
    });
  };

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
        {!isLoading && selectedIds.size === 0 && (
          <span className="text-xs font-medium text-muted-foreground">
            共 {total} 条记录
          </span>
        )}
        {selectedIds.size > 0 && (
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-muted-foreground">
              已选 {selectedIds.size} 项
            </span>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setDeleteDialogOpen(true)}
            >
              <Trash2 className="size-4" />
              删除
            </Button>
          </div>
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
                  <TableHead className="w-[40px] font-medium">
                    <Checkbox
                      checked={
                        allOnPageSelected
                          ? true
                          : someOnPageSelected
                            ? 'indeterminate'
                            : false
                      }
                      onCheckedChange={toggleSelectAll}
                      aria-label="全选当前页"
                      disabled={rowIds.length === 0}
                    />
                  </TableHead>
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
                      <TableCell colSpan={6} className="py-3">
                        <Skeleton className="h-5 w-full" />
                      </TableCell>
                    </TableRow>
                  ))
                ) : rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="h-40 text-center">
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
                    const canDelete = isDeletableStatus(item.status);
                    return (
                    <TableRow
                      key={item.id}
                      className="group cursor-pointer transition-colors hover:bg-muted/40"
                      onClick={() => navigate(targetPath)}
                    >
                      <TableCell className="w-[40px]">
                        {canDelete ? (
                          <Checkbox
                            checked={selectedIds.has(item.id)}
                            onCheckedChange={() => toggleRow(item.id)}
                            onClick={(e) => e.stopPropagation()}
                            aria-label={`选择 ${item.original_filename}`}
                          />
                        ) : (
                          <TooltipProvider delayDuration={200}>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span
                                  className="inline-flex"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <Checkbox
                                    disabled
                                    aria-label={`${item.original_filename} 批改完成后方可删除`}
                                  />
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="right">
                                <p className="text-xs">批改完成后方可删除</p>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        )}
                      </TableCell>
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

      <AlertDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              即将删除 {selectedIds.size} 条批改记录,此操作不可撤销,关联的 PDF 文件和对话记录将一并清除。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={deleteMutation.isPending}
              onClick={handleConfirmDelete}
            >
              {deleteMutation.isPending ? '删除中...' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
