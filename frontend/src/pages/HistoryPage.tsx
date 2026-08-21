import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { toast } from 'sonner';
import { ChevronLeft, ChevronRight, FileText, History, Trash2 } from 'lucide-react';
import {
  useSubmissions,
  useSubmissionsCount,
  useBatchDeleteSubmissions,
  type SubmissionOut,
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
import { StatusBadge } from '@/components/history/StatusBadge';
import { useLanguage } from '@/i18n';
import {
  isDeletableStatus,
  resolveDeleteError,
  shouldMoveToPreviousPage,
} from '@/lib/submissionStatus';

const PAGE_SIZE = 10;

export default function HistoryPage() {
  const navigate = useNavigate();
  const { t } = useLanguage();
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
  // 总数独立拉取,不随列表 15s 轮询(避免每次轮询都算 COUNT)。
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
        toast.success(`${t('已删除')} ${res.deleted_count} ${t('条记录')}`);
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
            {t('历史记录')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('查看已上传作业的批改状态与评分结果')}
          </p>
        </div>
        {!isLoading && selectedIds.size === 0 && (
          <span className="text-xs font-medium text-muted-foreground">
            {t('共')} {total} {t('条记录')}
          </span>
        )}
        {selectedIds.size > 0 && (
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-muted-foreground">
              {t('已选')} {selectedIds.size} {t('项')}
            </span>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setDeleteDialogOpen(true)}
            >
              <Trash2 className="size-4" />
              {t('删除')}
            </Button>
          </div>
        )}
      </div>

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-lg">{t('批改记录')}</CardTitle>
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
                      aria-label={t('全选当前页')}
                      disabled={rowIds.length === 0}
                    />
                  </TableHead>
                  <TableHead className="w-[40%] font-medium">{t('文件名')}</TableHead>
                  <TableHead className="w-[120px] font-medium">{t('状态')}</TableHead>
                  <TableHead className="w-[90px] font-medium">{t('分数')}</TableHead>
                  <TableHead className="w-[180px] font-medium">{t('上传时间')}</TableHead>
                  <TableHead className="w-[80px] font-medium">{t('操作')}</TableHead>
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
                        <span className="text-sm">{t('暂无批改记录')}</span>
                        <Button
                          size="sm"
                          onClick={() => navigate('/questions')}
                        >
                          {t('前往题目库')}
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
                            aria-label={`${t('选择')} ${item.original_filename}`}
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
                                    aria-label={`${item.original_filename} ${t('批改完成后方可删除')}`}
                                  />
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="right">
                                <p className="text-xs">{t('批改完成后方可删除')}</p>
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
                          {t('查看')}
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
                {t('上一页')}
              </Button>
              <span className="text-sm text-muted-foreground">
                {t('第')} <span className="font-semibold text-foreground">{currentPage}</span> / {totalPages} {t('页')}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={currentPage >= totalPages || isPlaceholderData}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                {t('下一页')}
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
            <AlertDialogTitle>{t('确认删除')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('即将删除')} {selectedIds.size} {t('条批改记录，此操作不可撤销，关联的 PDF 文件将一并清除。')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              {t('取消')}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={deleteMutation.isPending}
              onClick={handleConfirmDelete}
            >
              {deleteMutation.isPending ? t('删除中...') : t('确认删除')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
