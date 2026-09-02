import { Loader2 } from 'lucide-react';
import { type Question } from '@/api/questions';
import { Checkbox } from '@/components/ui/checkbox';
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
import { useLanguage } from '@/i18n';

export interface QuestionDanger {
  type: 'delete' | 'replace';
  question: Question;
  file?: File;
}

interface QuestionDangerDialogProps {
  danger: QuestionDanger | null;
  confirmation: string;
  acknowledgedDeletion: boolean;
  isPending: boolean;
  onConfirmationChange: (value: string) => void;
  onAcknowledgedDeletionChange: (value: boolean) => void;
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}

export function QuestionDangerDialog({
  danger,
  confirmation,
  acknowledgedDeletion,
  isPending,
  onConfirmationChange,
  onAcknowledgedDeletionChange,
  onConfirm,
  onOpenChange,
}: QuestionDangerDialogProps) {
  const { t } = useLanguage();

  return (
    <AlertDialog
      open={Boolean(danger)}
      onOpenChange={onOpenChange}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {danger?.type === 'delete' ? t('删除题目') : t('使用新版替换题目')}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {t('此操作会永久删除该题目关联的')} {danger?.question.submission_count ?? 0}{' '}
            {t('条批改记录和学生 PDF，无法恢复。请输入题目名称确认：')}
            <strong className="mt-2 block text-foreground">{danger?.question.name}</strong>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {danger?.type === 'replace' && (danger.question.submission_count ?? 0) > 0 && (
          <label className="flex items-start gap-2.5 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            <Checkbox
              checked={acknowledgedDeletion}
              onCheckedChange={(value) => onAcknowledgedDeletionChange(value === true)}
              className="mt-0.5 data-[state=checked]:bg-destructive data-[state=checked]:text-destructive-foreground"
            />
            <span>
              {t('我已知晓：替换成功后将永久删除上述')} {danger.question.submission_count}{' '}
              {t('条历史批改记录及其学生 PDF，不可恢复。')}
            </span>
          </label>
        )}
        <Input
          value={confirmation}
          onChange={(event) => onConfirmationChange(event.target.value)}
          placeholder={t('输入完整题目名称')}
        />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>{t('取消')}</AlertDialogCancel>
          <AlertDialogAction
            disabled={
              confirmation !== danger?.question.name ||
              isPending ||
              (danger?.type === 'replace' &&
                (danger.question.submission_count ?? 0) > 0 &&
                !acknowledgedDeletion)
            }
            onClick={(event) => {
              event.preventDefault();
              onConfirm();
            }}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isPending && <Loader2 className="animate-spin" />}
            {t('确认并永久')}{danger?.type === 'delete' ? t('删除') : t('替换')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
