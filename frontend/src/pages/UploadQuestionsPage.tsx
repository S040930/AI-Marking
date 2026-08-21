import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Upload, Settings2 } from 'lucide-react';
import { toast } from 'sonner';
import { useCreateQuestion, type Question } from '@/api/questions';
import { useConfigProfiles } from '@/api/config';
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

export default function UploadQuestionsPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const createMutation = useCreateQuestion();
  const { data: profiles } = useConfigProfiles();
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [profileId, setProfileId] = useState<number | null>(null);

  // 配置项目加载后默认选中默认项目；仅在用户尚未手动选择时生效
  useEffect(() => {
    if (profiles?.length && profileId === null) {
      const defaultProfile =
        profiles.find((profile) => profile.is_default) ?? profiles[0];
      if (defaultProfile) setProfileId(defaultProfile.id);
    }
  }, [profiles, profileId]);

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
  const hasUploaded = entries.some((entry) => entry.status === 'success');
  const canSubmit =
    entries.some((entry) => entry.status === 'ready') && !uploading;

  const uploadOne = (entry: UploadEntry, retry = false) => {
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
      { file: entry.file, name, configProfileId: profileId ?? undefined },
      {
        onSuccess: (question: Question) => {
          updateEntry(entry.id, {
            status: 'success',
            name: question.name,
          });
          toast.success(
            `${t('「')}${question.name}${t('」已上传，正在识别')}`,
          );
        },
        onError: (error) => {
          updateEntry(entry.id, {
            status: 'error',
            error: errorMessage(error),
          });
          if (!retry) toast.error(errorMessage(error));
        },
      },
    );
  };

  const uploadAll = () => {
    const ready = entries.filter((entry) => entry.status === 'ready');
    if (!ready.length) return;
    ready.forEach((entry) => uploadOne(entry));
  };

  const removeEntry = (id: string) => {
    setEntries((prev) => prev.filter((entry) => entry.id !== id));
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t('上传题目')}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t('批量上传题目 PDF，上传后自动进行 OCR 识别，识别完成后即可在题目库中复用。')}
        </p>
      </div>

      <Card className="elevated-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t('上传题目文件')}</CardTitle>
          <CardDescription>
            {t('仅支持 PDF 格式，单个文件不超过 50MB，可一次选择多个文件。')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 pt-4">
          <div className="flex items-center gap-3">
            <label className="shrink-0 text-sm font-medium text-foreground">
              {t('配置项目')}
            </label>
            {profiles?.length ? (
              <select
                aria-label={t('上传题目使用的配置项目')}
                value={profileId ?? ''}
                onChange={(event) =>
                  setProfileId(
                    event.target.value ? Number(event.target.value) : null,
                  )
                }
                className="h-9 rounded-md border border-input bg-background px-2.5 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.is_default ? '★ ' : ''}
                    {profile.name}
                  </option>
                ))}
              </select>
            ) : (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Settings2 className="size-4" />
                <span>{t('暂无配置项目，请先到系统设置创建')}</span>
                <Button
                  variant="link"
                  size="sm"
                  className="px-0"
                  onClick={() => navigate('/settings')}
                >
                  {t('前往系统设置')}
                </Button>
              </div>
            )}
          </div>

          <UploadZone
            entries={entries}
            onAddFiles={addFiles}
            onRemove={removeEntry}
            onClear={() => setEntries([])}
            onRename={(id, name) => updateEntry(id, { name })}
            onRetry={(id) => {
              const entry = entries.find((e) => e.id === id);
              if (entry) uploadOne(entry, true);
            }}
          />

          <div className="flex items-center justify-between border-t pt-4">
            <p className="text-xs text-muted-foreground">
              {hasUploaded
                ? t('已上传的文件正在后台识别，可前往题目库查看进度。')
                : t('上传成功后会自动进入识别队列。')}
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
