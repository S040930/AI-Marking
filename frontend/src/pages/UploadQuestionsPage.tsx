import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, Upload } from 'lucide-react';
import { toast } from 'sonner';
import {
  useCreateQuestion,
  useQuestionWatch,
  useRetryQuestionOcr,
  type Question,
} from '@/api/questions';
import { queryKeys } from '@/api/queryKeys';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { UploadZone, type UploadEntry } from '@/components/questions/UploadZone';
import { errorMessage } from '@/lib/questionErrors';
import { useLanguage } from '@/i18n';
import { PageHeader } from '@/components/common/PageHeader';

export default function UploadQuestionsPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const createMutation = useCreateQuestion();
  const retryOcrMutation = useRetryQuestionOcr();
  const [entries, setEntries] = useState<UploadEntry[]>([]);

  const addFiles = (files: File[]) => {
    const next = files.map((file) => ({
      id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      name: file.name.replace(/\.pdf$/i, ''),
      status: 'ready' as const,
    }));
    setEntries((prev) => [...prev, ...next]);
  };

  const updateEntry = (id: string, patch: Partial<UploadEntry>) => {
    setEntries((prev) =>
      prev.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)),
    );
  };

  const uploading = entries.some((entry) => entry.status === 'uploading');
  const canSubmit =
    entries.some((entry) => entry.status === 'ready') && !uploading;

  // 当前正在等待 OCR 的条目：同一时刻只轮询一个，全部收敛后统一跳转
  const watchingEntry = entries.find((entry) => entry.status === 'watching');
  const watched = useQuestionWatch(watchingEntry?.questionId ?? null);

  useEffect(() => {
    if (!watchingEntry || !watched.data) return;
    const status = watched.data.status;
    if (status === 'ready') {
      updateEntry(watchingEntry.id, { status: 'success' });
      toast.success(`${t('「')}${watchingEntry.name}${t('」识别完成')}`);
    } else if (status === 'failed') {
      updateEntry(watchingEntry.id, {
        status: 'error',
        error: watched.data.error_message ?? t('OCR 识别失败'),
      });
      toast.error(`${t('「')}${watchingEntry.name}${t('」OCR 识别失败')}`);
    }
    // watched.data 变化即重新评估；queryKey 随 watchingEntry.id 切换
  }, [watchingEntry, watched.data, t]);

  // 所有条目都成功识别后，携带题目名跳转到上传作业页
  useEffect(() => {
    if (!entries.length) return;
    const statuses = entries.map((entry) => entry.status);
    if (statuses.every((status) => status === 'success')) {
      const names = entries.map((entry) => entry.name);
      sessionStorage.setItem(
        'ocr-ready-questions',
        JSON.stringify({ names, at: Date.now() }),
      );
      navigate('/submissions/upload');
    }
  }, [entries, navigate]);

  const uploadOne = (entry: UploadEntry) => {
    const name = entry.name.trim();
    if (!name) {
      updateEntry(entry.id, {
        status: 'error',
        error: t('题目名称不能为空'),
      });
      return;
    }
    updateEntry(entry.id, { status: 'uploading', error: undefined });
    createMutation.mutate(
      { file: entry.file, name },
      {
        onSuccess: (question: Question) => {
          // 重试时清除上次的轮询结果，避免旧 failed 缓存立刻覆盖新状态
          queryClient.removeQueries({
            queryKey: queryKeys.questions.watch(question.id),
          });
          updateEntry(entry.id, {
            status: 'watching',
            questionId: question.id,
            name: question.name,
          });
          toast.success(
            `${t('「')}${question.name}${t('」已上传，等待 OCR 识别')}`,
          );
        },
        onError: (error) => {
          updateEntry(entry.id, {
            status: 'error',
            error: errorMessage(error),
          });
          toast.error(errorMessage(error));
        },
      },
    );
  };

  // 重试分流:OCR 失败(已有 questionId)走 retry-ocr 复用同一题目;
  // 上传本身失败(无 questionId)时再走一次 create——重复 create 会 409。
  const retryOne = (entry: UploadEntry) => {
    if (!entry.questionId) {
      uploadOne(entry);
      return;
    }
    updateEntry(entry.id, { status: 'uploading', error: undefined });
    retryOcrMutation.mutate(
      { id: entry.questionId, file: entry.file },
      {
        onSuccess: (question: Question) => {
          // 清除上次的轮询结果，避免旧 failed 缓存立刻覆盖新状态
          queryClient.removeQueries({
            queryKey: queryKeys.questions.watch(question.id),
          });
          updateEntry(entry.id, {
            status: 'watching',
            questionId: question.id,
          });
          toast.success(
            `${t('「')}${question.name}${t('」已上传，等待 OCR 识别')}`,
          );
        },
        onError: (error) => {
          updateEntry(entry.id, {
            status: 'error',
            error: errorMessage(error),
          });
          toast.error(errorMessage(error));
        },
      },
    );
  };

  const uploadAll = () => {
    const ready = entries.filter((entry) => entry.status === 'ready');
    if (!ready.length) return;
    ready.forEach((entry) => uploadOne(entry));
  };  const removeEntry = (id: string) => {
    setEntries((prev) => prev.filter((entry) => entry.id !== id));
  };

  const waitingOcr = entries.some(
    (entry) => entry.status === 'watching' || entry.status === 'uploading',
  );

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title={t('上传题目')}
        description={t(
          '批量上传题目 PDF，系统会等待 OCR 识别完成后引导你上传作业答案。',
        )}
      />

      <Card className="elevated-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t('上传题目文件')}</CardTitle>
          <CardDescription>
            {t('仅支持 PDF 格式，单个文件不超过 50MB，可一次选择多个文件。')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 pt-4">
          <UploadZone
            entries={entries}
            onAddFiles={addFiles}
            onRemove={removeEntry}
            onClear={() => setEntries([])}
            onRename={(id, name) => updateEntry(id, { name })}
            onRetry={(id) => {
              const entry = entries.find((e) => e.id === id);
              if (entry) retryOne(entry);
            }}
          />

          <div className="flex items-center justify-between border-t pt-4">
            <p className="text-xs text-muted-foreground">
              {waitingOcr
                ? t('正在等待 OCR 识别，全部识别完成后将进入上传作业页…')
                : t('上传后会自动等待 OCR 识别，完成后进入上传作业页。')}
            </p>
            <Button onClick={uploadAll} disabled={!canSubmit}>
              {createMutation.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Upload />
              )}
              {t('上传')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
