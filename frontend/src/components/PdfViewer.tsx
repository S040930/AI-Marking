import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  FileText,
  AlertCircle,
  BookOpen,
  ChevronLeft,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';

interface PdfViewerProps {
  submissionId: number;
  filename: string;
  status: string;
  className?: string;
}

const STATUS_BADGE: Record<string, { label: string; className: string }> = {
  ready_for_review: {
    label: '待审阅',
    className:
      'border-primary/20 bg-primary/[0.06] text-primary',
  },
  reviewed: {
    label: '已审阅',
    className:
      'border-success/20 bg-success/10 text-success',
  },
  failed: {
    label: '失败',
    className:
      'border-destructive/20 bg-destructive/10 text-destructive',
  },
};

export function PdfViewer({ submissionId, filename, status, className }: PdfViewerProps) {
  const navigate = useNavigate();
  const [type, setType] = useState<'submission' | 'question'>('submission');
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  const pdfUrl = `/api/submissions/${submissionId}/pdf?type=${type}`;
  const statusBadge = STATUS_BADGE[status] ?? {
    label: status,
    className:
      'border-muted-foreground/20 bg-muted text-muted-foreground',
  };

  const handleTypeChange = (next: 'submission' | 'question') => {
    if (next === type) return;
    setType(next);
    setIsLoading(true);
    setHasError(false);
  };

  return (
    <div
      className={`relative flex flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm ${className ?? ''}`}
    >
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b border-border bg-muted/50 px-3 py-2">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => navigate('/history')}
          className="size-8 rounded-full text-muted-foreground hover:text-foreground"
          title="返回历史"
        >
          <ChevronLeft className="size-4" />
        </Button>

        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          {filename}
        </span>

        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusBadge.className}`}
        >
          {statusBadge.label}
        </span>

        <div className="hidden items-center rounded-full bg-background p-0.5 shadow-sm sm:inline-flex">
          <button
            type="button"
            onClick={() => handleTypeChange('submission')}
            className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
              type === 'submission'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <FileText className="size-3" />
            作业
          </button>
          <button
            type="button"
            onClick={() => handleTypeChange('question')}
            className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all ${
              type === 'question'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <BookOpen className="size-3" />
            题目
          </button>
        </div>
      </div>

      {/* Mobile tab fallback */}
      <div className="flex items-center justify-center border-b border-border bg-muted/30 py-1.5 sm:hidden">
        <div className="inline-flex items-center rounded-full bg-background p-0.5 shadow-sm">
          <button
            type="button"
            onClick={() => handleTypeChange('submission')}
            className={`flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition-all ${
              type === 'submission'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <FileText className="size-3.5" />
            学生作业
          </button>
          <button
            type="button"
            onClick={() => handleTypeChange('question')}
            className={`flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition-all ${
              type === 'question'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <BookOpen className="size-3.5" />
            作业题目
          </button>
        </div>
      </div>

      {isLoading && !hasError && (
        <div className="absolute inset-0 top-[83px] z-10 flex flex-col gap-3 bg-card p-6 sm:top-[45px]">
          <Skeleton className="h-6 w-1/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="mt-4 h-64 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      )}

      {hasError ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
          <div className="flex size-12 items-center justify-center rounded-full bg-muted">
            <AlertCircle className="size-6 text-muted-foreground" />
          </div>
          <p className="text-sm font-medium text-foreground">PDF 加载失败</p>
          <p className="max-w-xs text-xs text-muted-foreground">
            文件可能已过期或无法访问。请返回历史记录重新上传。
          </p>
        </div>
      ) : (
        <iframe
          src={pdfUrl}
          title={type === 'question' ? '作业题目' : '学生作业'}
          className={`min-h-0 w-full flex-1 ${isLoading ? 'opacity-0' : 'animate-soft-fade-in opacity-100'}`}
          onLoad={() => setIsLoading(false)}
          onError={() => {
            setIsLoading(false);
            setHasError(true);
          }}
        />
      )}
    </div>
  );
}

export function PdfViewerPlaceholder() {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b border-border bg-muted/50 px-3 py-2">
        <div className="size-8 rounded-full bg-muted" />
        <Skeleton className="h-5 w-40" />
        <Skeleton className="ml-auto h-5 w-16 rounded-full" />
      </div>
      <Skeleton className="m-5 flex-1" />
    </div>
  );
}
