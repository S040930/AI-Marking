import axios from 'axios';
import { toast } from 'sonner';
import { Loader2, AlertCircle, RotateCcw } from 'lucide-react';
import { useRetrySubmission } from '@/api/submissions';
import type { SubmissionDetail } from '@/api/submissions';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';

export function FailedRecoveryPanel({ data }: { data: SubmissionDetail }) {
  const { t } = useLanguage();
  const retryMutation = useRetrySubmission(data.id);

  const retry = () => {
    retryMutation.mutate(undefined, {
      onSuccess: () => toast.success(t('已重新进入批改队列')),
      onError: (error) => {
        const detail = axios.isAxiosError(error)
          ? error.response?.data?.detail
          : null;
        toast.error(
          typeof detail === 'string'
            ? detail
            : error.message || t('重新批改失败'),
        );
      },
    });
  };

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-md rounded-2xl border border-destructive/20 bg-white p-6 shadow-sm">
        <AlertCircle className="mb-4 size-8 text-destructive" />
        <h2 className="text-lg font-semibold">{t('本次批改失败')}</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {data.error_message || t('批改过程中发生未知错误')}
        </p>
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          {t('可以使用原文件重新批改；如果文件内容有问题，请让编程助手重新提交学生作业。')}
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Button
            disabled={retryMutation.isPending}
            onClick={retry}
          >
            {retryMutation.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <RotateCcw />
            )}
            {t('使用原文件重试')}
          </Button>
        </div>
      </div>
    </div>
  );
}
