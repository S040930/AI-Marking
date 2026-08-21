import { type Question, useGradingPrompt } from '@/api/questions';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { CopyablePromptPanel } from '@/components/common/CopyablePromptPanel';
import { buildGradingPrompt } from '@/lib/gradingPrompt';
import { useLanguage } from '@/i18n';
import { RefreshCw, TriangleAlert } from 'lucide-react';

interface QuestionPromptDialogProps {
  question: Question | null;
  onOpenChange: (open: boolean) => void;
}

export function QuestionPromptDialog({
  question,
  onOpenChange,
}: QuestionPromptDialogProps) {
  const { locale, t } = useLanguage();
  const {
    data: promptData,
    isLoading,
    isError,
    refetch,
  } = useGradingPrompt(question ? question.id : null);

  return (
    <AlertDialog open={Boolean(question)} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('批改提示词')}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('复制后在编程助手（如 Codex）中粘贴，并在对话中上传学生作业 zip（一名学生一个 zip：报告 PDF + 代码文件）。')}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {question && isLoading && (
          <div className="space-y-3">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        )}

        {question && isError && (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>{t('提示词生成失败')}</AlertTitle>
            <AlertDescription>
              {t('该题目尚未完成 OCR 识别，无法生成批改提示词。请先在题目库确认识别完成后再试。')}
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => refetch()}
              >
                <RefreshCw />
                {t('重试')}
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {question && promptData && (
          <CopyablePromptPanel
            prompt={buildGradingPrompt(
              { questionName: question.name, questionId: question.id },
              locale,
            )}
          />
        )}

        <AlertDialogFooter>
          <AlertDialogCancel>{t('关闭')}</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
