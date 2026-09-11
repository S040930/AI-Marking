import { useRef, useState, type DragEvent } from 'react';
import {
  Check,
  FileText,
  FileUp,
  Loader2,
  RotateCcw,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  DOCUMENT_INPUT_ACCEPT,
  MAX_DOCUMENT_SIZE_BYTES,
} from '@/lib/documentUpload';
import { cn } from '@/lib/utils';
import { useLanguage } from '@/i18n';

export type UploadEntryStatus =
  | 'ready'
  | 'uploading'
  | 'watching'
  | 'success'
  | 'error';

export interface UploadEntry {
  id: string;
  file: File;
  name: string;
  status: UploadEntryStatus;
  /** 上传成功后由后端返回的题目 ID，用于等待 OCR 识别 */
  questionId?: string;
  error?: string;
}

interface UploadZoneProps {
  entries: UploadEntry[];
  onAddFiles: (files: File[]) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
  onRename: (id: string, name: string) => void;
  onRetry: (id: string) => void;
}

function isPdf(file: File): boolean {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const statusMeta: Record<UploadEntryStatus, [string, string]> = {
  ready: ['待上传', 'text-muted-foreground'],
  uploading: ['上传中', 'text-primary'],
  watching: ['识别中', 'text-primary'],
  success: ['识别完成', 'text-emerald-600'],
  error: ['失败', 'text-destructive'],
};

export function UploadZone({
  entries,
  onAddFiles,
  onRemove,
  onClear,
  onRename,
  onRetry,
}: UploadZoneProps) {
  const { t } = useLanguage();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const acceptFiles = (files: File[]) => {
    const valid: File[] = [];
    for (const file of files) {
      if (!isPdf(file)) {
        toast.error(
          `${t('「')}${file.name}${t('」不是 PDF 文件，已跳过')}`,
        );
        continue;
      }
      if (file.size > MAX_DOCUMENT_SIZE_BYTES) {
        toast.error(
          `${t('「')}${file.name}${t('」超过 50MB，已跳过')}`,
        );
        continue;
      }
      const duplicate = entries.some(
        (entry) => entry.file.name === file.name && entry.file.size === file.size,
      );
      if (duplicate) {
        toast.error(`${t('「')}${file.name}${t('」已在列表中')}`);
        continue;
      }
      valid.push(file);
    }
    if (valid.length) onAddFiles(valid);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragOver(false);
    acceptFiles(Array.from(event.dataTransfer.files));
  };

  const uploading = entries.some(
    (entry) => entry.status === 'uploading' || entry.status === 'watching',
  );

  return (
    <div className="space-y-4">
      <div
        role="button"
        tabIndex={0}
        aria-label={t('点击选择或拖拽 PDF 文件到此处')}
        onClick={() => !uploading && inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        onPaste={(event) => acceptFiles(Array.from(event.clipboardData.files))}
        onDragOver={(event) => {
          event.preventDefault();
          if (!uploading) setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors',
          isDragOver
            ? 'border-primary bg-primary/5'
            : 'border-muted-foreground/30 hover:border-primary/50 hover:bg-muted/40',
          uploading && 'pointer-events-none opacity-60',
        )}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={DOCUMENT_INPUT_ACCEPT}
          className="hidden"
          onChange={(event) => {
            acceptFiles(Array.from(event.target.files ?? []));
            event.target.value = '';
          }}
        />
        <div className="flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <FileUp className="size-6" />
        </div>
        <p className="font-medium text-foreground">
          {t('拖拽 PDF 到此处，或点击选择文件')}
        </p>
        <p className="text-xs text-muted-foreground">
          {t('支持一次选择多个文件，单个不超过 50MB；也可直接粘贴文件')}
        </p>
      </div>

      {entries.length > 0 && (
        <ul className="divide-y rounded-xl border">
          {entries.map((entry, index) => {
            const [statusLabel, statusClass] = statusMeta[entry.status];
            return (
              <li
                key={entry.id}
                className="flex items-center gap-3 px-4 py-3"
              >
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  {entry.status === 'uploading' || entry.status === 'watching' ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : entry.status === 'success' ? (
                    <Check className="size-4 text-emerald-600" />
                  ) : (
                    <FileText className="size-4" />
                  )}
                </span>
                <div className="min-w-0 flex-1">
                  <Input
                    aria-label={`${t('题目名称')} ${index + 1}`}
                    value={entry.name}
                    disabled={entry.status !== 'ready'}
                    onChange={(event) => onRename(entry.id, event.target.value)}
                    className="h-8"
                  />
                  <p className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="truncate">{entry.file.name}</span>
                    <span>{formatSize(entry.file.size)}</span>
                    <span className={cn('font-medium', statusClass)}>
                      {t(statusLabel)}
                    </span>
                  </p>
                  {entry.error && (
                    <p className="mt-1 text-xs text-destructive">{entry.error}</p>
                  )}
                </div>
                {entry.status === 'error' && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onRetry(entry.id)}
                  >
                    <RotateCcw />{t('重试')}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`${t('移除')} ${entry.name}`}
                  disabled={entry.status === 'uploading'}
                  onClick={() => onRemove(entry.id)}
                >
                  <X />
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      {entries.length > 0 && (
        <div className="flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            disabled={uploading}
            onClick={onClear}
          >
            <X />{t('清空列表')}
          </Button>
        </div>
      )}
    </div>
  );
}
