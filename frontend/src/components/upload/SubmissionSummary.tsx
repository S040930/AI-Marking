import { BookOpen, Check, FileText, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface SubmissionSummaryProps {
  questionName: string;
  fileName: string;
  fileSize: number;
  onSubmit: () => void;
  onBack: () => void;
  isSubmitting: boolean;
  canSubmit: boolean;
}

export function SubmissionSummary({
  questionName,
  fileName,
  fileSize,
  onSubmit,
  onBack,
  isSubmitting,
  canSubmit,
}: SubmissionSummaryProps) {
  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-border bg-muted/30 p-4">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          批改内容确认
        </p>
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <BookOpen className="size-5" />
            </div>
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground">评分依据</p>
              <p className="truncate text-sm font-semibold text-foreground">
                {questionName}
              </p>
            </div>
          </div>
          <div className="h-px bg-border" />
          <div className="flex items-center gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-emerald/10 text-emerald">
              <FileText className="size-5" />
            </div>
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground">学生作业</p>
              <p className="truncate text-sm font-semibold text-foreground">
                {fileName}
              </p>
              <p className="text-xs text-muted-foreground">
                {fileSize.toFixed(2)} MB
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Button
          type="button"
          variant="outline"
          className="w-full sm:w-auto"
          onClick={onBack}
          disabled={isSubmitting}
        >
          返回修改
        </Button>
        <Button
          type="button"
          size="lg"
          className="btn-press w-full sm:w-auto text-base font-semibold"
          disabled={!canSubmit || isSubmitting}
          onClick={onSubmit}
        >
          {isSubmitting ? (
            <>
              <Loader2 className="animate-spin" />
              提交中...
            </>
          ) : (
            <>
              <Check className="size-4" />
              开始批改
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
