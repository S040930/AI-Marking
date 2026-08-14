import { useRef, useState } from 'react';
import axios from 'axios';
import {
  BookOpen,
  Eye,
  FileUp,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
} from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { toast } from 'sonner';
import {
  type Question,
  useChangeQuestionConfigProfile,
  useCreateQuestion,
  useDeleteQuestion,
  useQuestions,
  useRenameQuestion,
  useReplaceQuestion,
  useRetryQuestionOcr,
} from '@/api/questions';
import { useConfigProfiles } from '@/api/config';
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
import { DOCUMENT_INPUT_ACCEPT } from '@/lib/documentUpload';

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
  const [acknowledgedDeletion, setAcknowledgedDeletion] = useState(false);
  const createMutation = useCreateQuestion();
  const [uploadProfileId, setUploadProfileId] = useState<number | null>(null);
  const switchProfileMutation = useChangeQuestionConfigProfile();
  const { data: profiles } = useConfigProfiles();
  const renameMutation = useRenameQuestion();
  const retryMutation = useRetryQuestionOcr();
  const deleteMutation = useDeleteQuestion();
  const replaceMutation = useReplaceQuestion();
  const { data, isLoading } = useQuestions(search);
  const uploadRef = useRef<HTMLInputElement>(null);
  const retryRefs = useRef<Record<number, HTMLInputElement | null>>({});

  const uploadQuestion = (file?: File) => {
    if (!file) return;
    createMutation.mutate(
      { file, configProfileId: uploadProfileId ?? undefined },
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
    if (danger.type === 'delete') {
      deleteMutation.mutate(
        { id: danger.question.id, confirmationName: confirmation },
        {
          onSuccess: (result) => {
            toast.success(
              `题目已删除，清理了 ${result.deleted_submission_count} 条批改记录`,
            );
            setDanger(null);
            setConfirmation('');
            setAcknowledgedDeletion(false);
          },
          onError: (error) => toast.error(errorMessage(error)),
        },
      );
    } else if (danger.file) {
      replaceMutation.mutate(
        {
          id: danger.question.id,
          file: danger.file,
          confirmationName: confirmation,
          acknowledgeDeletion: acknowledgedDeletion,
        },
        {
          onSuccess: (result) => {
            toast.success(
              `新版已进入后台识别，成功后将清理 ${result.affected_submission_count} 条旧批改记录`,
            );
            setDanger(null);
            setConfirmation('');
            setAcknowledgedDeletion(false);
          },
          onError: (error) => toast.error(errorMessage(error)),
        },
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
          accept={DOCUMENT_INPUT_ACCEPT}
          className="hidden"
          onChange={(event) => {
            uploadQuestion(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          {profiles?.length ? (
            <select
              aria-label="上传题目使用的配置项目"
              value={uploadProfileId ?? ''}
              onChange={(event) =>
                setUploadProfileId(
                  event.target.value ? Number(event.target.value) : null,
                )
              }
              className="h-9 rounded-md border border-input bg-background px-2.5 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {profiles?.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.is_default ? '★ ' : ''}
                  {profile.name}
                </option>
              ))}
            </select>
          ) : null}
          <Button onClick={() => uploadRef.current?.click()}>
            {createMutation.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
            上传新题目
          </Button>
        </div>
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
            const replacementActive =
              question.replacement_status === 'pending' ||
              question.replacement_status === 'processing';
            const replacementLabel =
              question.replacement_status === 'pending'
                ? '新版排队中'
                : question.replacement_status === 'processing'
                  ? '新版识别中'
                  : question.replacement_status === 'failed'
                    ? '新版识别失败'
                    : null;
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
                    <Badge
                      variant={
                        question.replacement_status === 'failed'
                          ? 'destructive'
                          : meta[1]
                      }
                    >
                      {(question.status === 'ocr_processing' ||
                        question.replacement_status === 'processing') && (
                        <Loader2 className="mr-1 size-3 animate-spin" />
                      )}
                      {replacementLabel ?? meta[0]}
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
                      OCR 失败：{question.error_message}。请重新选择 PDF 上传。
                    </p>
                  )}
                  {question.replacement_error_message && (
                    <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-700">
                      新版识别失败：{question.replacement_error_message}。旧版题目仍可继续使用。
                    </p>
                  )}
                  <div className="flex items-center gap-2 border-t pt-3">
                    <span className="text-xs text-muted-foreground">
                      配置项目
                    </span>
                    {profiles?.length ? (
                      <select
                        aria-label={`切换 ${question.name} 的配置项目`}
                        value={question.config_profile_id}
                        disabled={
                          switchProfileMutation.isPending ||
                          question.status === 'pending' ||
                          question.status === 'ocr_processing' ||
                          replacementActive
                        }
                        onChange={(event) => {
                          const next = Number(event.target.value);
                          if (next === question.config_profile_id) return;
                          switchProfileMutation.mutate(
                            { id: question.id, configProfileId: next },
                            {
                              onSuccess: () =>
                                toast.success('配置项目已更新'),
                              onError: (error) =>
                                toast.error(errorMessage(error)),
                            },
                          );
                        }}
                        className="h-8 rounded-md border border-input bg-background px-2 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
                      >
                        {profiles?.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.is_default ? '★ ' : ''}
                            {profile.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="text-sm text-muted-foreground">
                        #{question.config_profile_id}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2 border-t pt-3">
                    <Button variant="outline" size="sm" asChild>
                      <a href={`/api/questions/${question.id}/pdf`} target="_blank">
                        <Eye />查看
                      </a>
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={replacementActive}
                      onClick={() => rename(question)}
                    >
                      <Pencil />重命名
                    </Button>
                    {question.status === 'failed' && (
                      <>
                        <input
                          ref={(element) => {
                            if (element) retryRefs.current[question.id] = element;
                            else delete retryRefs.current[question.id];
                          }}
                          type="file"
                          accept={DOCUMENT_INPUT_ACCEPT}
                          aria-label={`重新上传 ${question.name} 文件`}
                          className="hidden"
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            if (file) {
                              retryMutation.mutate(
                                { id: question.id, file },
                                {
                                  onSuccess: () =>
                                    toast.success('文件已重新上传，正在识别'),
                                  onError: (error) =>
                                    toast.error(errorMessage(error)),
                                },
                              );
                            }
                            event.target.value = '';
                          }}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => retryRefs.current[question.id]?.click()}
                        >
                          <FileUp />重新上传文件
                        </Button>
                      </>
                    )}
                    <label className={`inline-flex ${replacementActive ? 'pointer-events-none opacity-50' : ''}`}>
                      <input
                        type="file"
                        accept={DOCUMENT_INPUT_ACCEPT}
                        className="hidden"
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (file) setDanger({ type: 'replace', question, file });
                          event.target.value = '';
                        }}
                      />
                      <Button variant="ghost" size="sm" disabled={replacementActive} asChild>
                        <span><FileUp />上传新版</span>
                      </Button>
                    </label>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={replacementActive}
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
          <p className="mt-1 text-sm text-muted-foreground">上传第一份 PDF 题目开始使用</p>
        </div>
      )}

      <AlertDialog
        open={Boolean(danger)}
        onOpenChange={(open) => {
          if (!open && !isDangerPending) {
            setDanger(null);
            setConfirmation('');
            setAcknowledgedDeletion(false);
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
          {danger?.type === 'replace' && (danger.question.submission_count ?? 0) > 0 && (
            <label className="flex items-start gap-2.5 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              <Checkbox
                checked={acknowledgedDeletion}
                onCheckedChange={(value) => setAcknowledgedDeletion(value === true)}
                className="mt-0.5 data-[state=checked]:bg-destructive data-[state=checked]:text-destructive-foreground"
              />
              <span>
                我已知晓：替换成功后将永久删除上述 {danger.question.submission_count}{' '}
                条历史批改记录及其学生 PDF，不可恢复。
              </span>
            </label>
          )}
          <Input
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder="输入完整题目名称"
          />
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDangerPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={
                confirmation !== danger?.question.name ||
                isDangerPending ||
                (danger?.type === 'replace' &&
                  (danger.question.submission_count ?? 0) > 0 &&
                  !acknowledgedDeletion)
              }
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
