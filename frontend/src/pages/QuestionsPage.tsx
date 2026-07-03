import { useRef, useState } from 'react';
import axios from 'axios';
import {
  BookOpen,
  Eye,
  FileUp,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  type Question,
  useCreateQuestion,
  useDeleteQuestion,
  useQuestions,
  useRenameQuestion,
  useReplaceQuestion,
  useRetryQuestionOcr,
} from '@/api/questions';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
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

function errorMessage(error: Error): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : detail?.message ?? error.message;
  }
  return error.message;
}

const statusMeta = {
  pending: ['等待识别', 'secondary'],
  ocr_processing: ['正在识别', 'secondary'],
  ready: ['可使用', 'default'],
  failed: ['识别失败', 'destructive'],
} as const;

export default function QuestionsPage() {
  const [search, setSearch] = useState('');
  const [danger, setDanger] = useState<{
    type: 'delete' | 'replace';
    question: Question;
    file?: File;
  } | null>(null);
  const [confirmation, setConfirmation] = useState('');
  const createMutation = useCreateQuestion();
  const renameMutation = useRenameQuestion();
  const retryMutation = useRetryQuestionOcr();
  const deleteMutation = useDeleteQuestion();
  const replaceMutation = useReplaceQuestion();
  const { data, isLoading } = useQuestions(search);
  const uploadRef = useRef<HTMLInputElement>(null);

  const uploadQuestion = (file?: File) => {
    if (!file) return;
    createMutation.mutate(
      { file },
      {
        onSuccess: () => toast.success('题目已上传，正在进行 OCR 识别'),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  const rename = (question: Question) => {
    const name = window.prompt('输入新的题目名称', question.name)?.trim();
    if (!name || name === question.name) return;
    renameMutation.mutate(
      { id: question.id, name },
      {
        onSuccess: () => toast.success('题目名称已更新'),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  const confirmDanger = () => {
    if (!danger || confirmation !== danger.question.name) return;
    const handlers = {
      onSuccess: (result: { deleted_submission_count: number }) => {
        toast.success(
          `${danger.type === 'delete' ? '题目已删除' : '题目已更新'}，清理了 ${result.deleted_submission_count} 条批改记录`,
        );
        setDanger(null);
        setConfirmation('');
      },
      onError: (error: Error) => toast.error(errorMessage(error)),
    };
    if (danger.type === 'delete') {
      deleteMutation.mutate(
        { id: danger.question.id, confirmationName: confirmation },
        handlers,
      );
    } else if (danger.file) {
      replaceMutation.mutate(
        {
          id: danger.question.id,
          file: danger.file,
          confirmationName: confirmation,
        },
        handlers,
      );
    }
  };

  const isDangerPending = deleteMutation.isPending || replaceMutation.isPending;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">题目库</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            题目只需上传并识别一次，之后可直接用于多份学生作业。
          </p>
        </div>
        <input
          ref={uploadRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => {
            uploadQuestion(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
        <Button onClick={() => uploadRef.current?.click()}>
          {createMutation.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
          上传新题目
        </Button>
      </div>

      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索题目名称或文件名"
          className="pl-9"
        />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="animate-spin text-primary" />
        </div>
      ) : data?.items.length ? (
        <div className="grid gap-4 md:grid-cols-2">
          {data.items.map((question) => {
            const meta = statusMeta[question.status];
            return (
              <Card key={question.id} className="transition-shadow hover:shadow-md">
                <CardContent className="space-y-4 p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 gap-3">
                      <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                        <BookOpen className="size-5" />
                      </div>
                      <div className="min-w-0">
                        <h2 className="truncate font-semibold">{question.name}</h2>
                        <p className="truncate text-xs text-muted-foreground">
                          {question.original_filename}
                        </p>
                      </div>
                    </div>
                    <Badge variant={meta[1]}>
                      {question.status === 'ocr_processing' && (
                        <Loader2 className="mr-1 size-3 animate-spin" />
                      )}
                      {meta[0]}
                    </Badge>
                  </div>
                  <div className="flex gap-5 text-sm text-muted-foreground">
                    <span>{question.submission_count} 份批改记录</span>
                    <span>
                      {question.last_used_at
                        ? `最近使用 ${new Date(question.last_used_at).toLocaleDateString()}`
                        : '尚未使用'}
                    </span>
                  </div>
                  {question.error_message && (
                    <p className="rounded-lg bg-destructive/10 p-3 text-xs text-destructive">
                      {question.error_message}
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2 border-t pt-3">
                    <Button variant="outline" size="sm" asChild>
                      <a href={`/api/questions/${question.id}/pdf`} target="_blank">
                        <Eye />查看
                      </a>
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => rename(question)}>
                      <Pencil />重命名
                    </Button>
                    {question.status === 'failed' && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => retryMutation.mutate(question.id)}
                      >
                        <RefreshCw />重新识别
                      </Button>
                    )}
                    <label className="inline-flex">
                      <input
                        type="file"
                        accept="application/pdf,.pdf"
                        className="hidden"
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (file) setDanger({ type: 'replace', question, file });
                          event.target.value = '';
                        }}
                      />
                      <Button variant="ghost" size="sm" asChild>
                        <span><FileUp />上传新版</span>
                      </Button>
                    </label>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => setDanger({ type: 'delete', question })}
                    >
                      <Trash2 />删除
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed py-20 text-center">
          <BookOpen className="mx-auto mb-3 size-10 text-muted-foreground/50" />
          <p className="font-medium">还没有可显示的题目</p>
          <p className="mt-1 text-sm text-muted-foreground">上传第一份题目 PDF 开始使用</p>
        </div>
      )}

      <AlertDialog
        open={Boolean(danger)}
        onOpenChange={(open) => {
          if (!open && !isDangerPending) {
            setDanger(null);
            setConfirmation('');
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {danger?.type === 'delete' ? '删除题目' : '使用新版替换题目'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              此操作会永久删除该题目关联的 {danger?.question.submission_count ?? 0}{' '}
              条批改记录、对话和学生 PDF，无法恢复。请输入题目名称确认：
              <strong className="mt-2 block text-foreground">{danger?.question.name}</strong>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Input
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder="输入完整题目名称"
          />
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDangerPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={confirmation !== danger?.question.name || isDangerPending}
              onClick={(event) => {
                event.preventDefault();
                confirmDanger();
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDangerPending && <Loader2 className="animate-spin" />}
              确认并永久{danger?.type === 'delete' ? '删除' : '替换'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
