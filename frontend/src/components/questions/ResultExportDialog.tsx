import { useEffect, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { type Question } from '@/api/questions';
import { useExportQuestionResults } from '@/api/submissions';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useLanguage } from '@/i18n';

interface ResultExportDialogProps {
  question: Question;
  onOpenChange: (open: boolean) => void;
}

export function ResultExportDialog({
  question,
  onOpenChange,
}: ResultExportDialogProps) {
  const { locale, t } = useLanguage();
  const [threshold, setThreshold] = useState('60');
  const exportMutation = useExportQuestionResults();
  const parsedThreshold = Number(threshold);
  const isValid =
    threshold.trim() !== '' &&
    Number.isFinite(parsedThreshold) &&
    parsedThreshold > 0 &&
    parsedThreshold <= 100;

  useEffect(() => {
    setThreshold('60');
  }, [question.id]);

  const startExport = () => {
    if (!isValid || exportMutation.isPending) return;
    exportMutation.mutate(
      {
        questionId: question.id,
        passThreshold: parsedThreshold,
        locale,
      },
      {
        onSuccess: () => {
          toast.success(t('Excel 成绩分析已生成'));
          onOpenChange(false);
        },
        onError: (error) => toast.error(error.message),
      },
    );
  };

  return (
    <AlertDialog
      open
      onOpenChange={(open) => !exportMutation.isPending && onOpenChange(open)}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('导出成绩分析')}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('将导出该题目下所有已审阅的最终成绩。')} {question.name}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2 py-2">
          <Label htmlFor="pass-threshold">{t('本次及格线（得分率 %）')}</Label>
          <Input
            id="pass-threshold"
            type="number"
            min="0.01"
            max="100"
            step="0.01"
            value={threshold}
            disabled={exportMutation.isPending}
            aria-invalid={!isValid}
            onChange={(event) => setThreshold(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') startExport();
            }}
          />
          <p className="text-xs text-muted-foreground">
            {t('分数段固定按得分率划分；此数值只决定及格率和是否达标。')}
          </p>
          {!isValid && (
            <p className="text-xs text-destructive">
              {t('请输入大于 0 且不超过 100 的数值')}
            </p>
          )}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={exportMutation.isPending}>
            {t('取消')}
          </AlertDialogCancel>
          <Button
            onClick={startExport}
            disabled={!isValid || exportMutation.isPending}
          >
            {exportMutation.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Download />
            )}
            {exportMutation.isPending
              ? t('正在生成...')
              : t('生成并下载 Excel')}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
