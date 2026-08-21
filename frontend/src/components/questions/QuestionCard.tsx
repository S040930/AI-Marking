import { useRef } from 'react';
import {
  BookOpen,
  Copy,
  Eye,
  FileUp,
  Loader2,
  Pencil,
  Trash2,
} from 'lucide-react';
import { type Question } from '@/api/questions';
import { type ConfigProfile } from '@/api/config';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { DOCUMENT_INPUT_ACCEPT } from '@/lib/documentUpload';
import { useLanguage } from '@/i18n';

const statusMeta = {
  pending: ['等待识别', 'secondary'],
  ocr_processing: ['正在识别', 'secondary'],
  ready: ['可使用', 'default'],
  failed: ['识别失败', 'destructive'],
} as const;

interface QuestionCardProps {
  question: Question;
  profiles: ConfigProfile[] | undefined;
  isSwitchingProfile: boolean;
  onRename: (question: Question) => void;
  onRetryUpload: (id: number, file: File) => void;
  onSwitchProfile: (id: number, configProfileId: number) => void;
  onReplace: (question: Question, file: File) => void;
  onDelete: (question: Question) => void;
  onCopyPrompt: (question: Question) => void;
}

export function QuestionCard({
  question,
  profiles,
  isSwitchingProfile,
  onRename,
  onRetryUpload,
  onSwitchProfile,
  onReplace,
  onDelete,
  onCopyPrompt,
}: QuestionCardProps) {
  const { t } = useLanguage();
  const retryRef = useRef<HTMLInputElement>(null);

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
    <Card className="transition-shadow hover:shadow-md">
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
            {t(replacementLabel ?? meta[0])}
          </Badge>
        </div>
        <div className="flex gap-5 text-sm text-muted-foreground">
          <span>{question.submission_count} {t('份批改记录')}</span>
          <span>
            {question.last_used_at
              ? `${t('最近使用')} ${new Date(question.last_used_at).toLocaleDateString()}`
              : t('尚未使用')}
          </span>
        </div>
        {question.error_message && (
          <p className="rounded-lg bg-destructive/10 p-3 text-xs text-destructive">
            {t('OCR 失败：')}{question.error_message}{t('。请重新选择 PDF 上传。')}
          </p>
        )}
        {question.replacement_error_message && (
          <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-700">
            {t('新版识别失败：')}{question.replacement_error_message}{t('。旧版题目仍可继续使用。')}
          </p>
        )}
        <div className="flex items-center gap-2 border-t pt-3">
          <span className="text-xs text-muted-foreground">
            {t('配置项目')}
          </span>
          {profiles?.length ? (
            <select
              aria-label={`${t('切换')} ${question.name} ${t('的配置项目')}`}
              value={question.config_profile_id}
              disabled={
                isSwitchingProfile ||
                question.status === 'pending' ||
                question.status === 'ocr_processing' ||
                replacementActive
              }
              onChange={(event) => {
                const next = Number(event.target.value);
                if (next === question.config_profile_id) return;
                onSwitchProfile(question.id, next);
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
            <a href={`/api/questions/${question.id}/pdf`} target="_blank" rel="noopener noreferrer">
              <Eye />{t('查看')}
            </a>
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={question.status !== 'ready' || replacementActive}
            onClick={() => onCopyPrompt(question)}
          >
            <Copy />{t('复制提示词')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={replacementActive}
            onClick={() => onRename(question)}
          >
            <Pencil />{t('重命名')}
          </Button>
          {question.status === 'failed' && (
            <>
              <input
                ref={retryRef}
                type="file"
                accept={DOCUMENT_INPUT_ACCEPT}
                aria-label={`重新上传 ${question.name} 文件`}
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onRetryUpload(question.id, file);
                  event.target.value = '';
                }}
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => retryRef.current?.click()}
              >
                <FileUp />{t('重新上传文件')}
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
                if (file) onReplace(question, file);
                event.target.value = '';
              }}
            />
            <Button variant="ghost" size="sm" disabled={replacementActive} asChild>
              <span><FileUp />{t('上传新版')}</span>
            </Button>
          </label>
          <Button
            variant="ghost"
            size="sm"
            disabled={replacementActive}
            className="text-destructive hover:text-destructive"
            onClick={() => onDelete(question)}
          >
            <Trash2 />{t('删除')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
