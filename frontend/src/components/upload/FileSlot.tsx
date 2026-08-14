import { useDropzone } from 'react-dropzone';
import { FileText, Inbox, X } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { DOCUMENT_DROPZONE_ACCEPT } from '@/lib/documentUpload';

export interface FileSlotProps {
  label: string;
  description: string;
  file: File | null;
  onFile: (file: File | null) => void;
  compact?: boolean;
}

export function FileSlot({
  label,
  description,
  file,
  onFile,
  compact = false,
}: FileSlotProps) {
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: DOCUMENT_DROPZONE_ACCEPT,
    maxFiles: 1,
    multiple: false,
    onDrop: (acceptedFiles) => {
      if (acceptedFiles.length > 0) {
        onFile(acceptedFiles[0]);
      }
    },
    onDropRejected: () => {
      toast.error('仅支持 PDF 文件');
    },
  });

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-semibold text-foreground">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <div
        {...getRootProps()}
        className={cn(
          'group relative flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 text-center transition-all duration-300',
          compact ? 'py-8' : 'py-10',
          isDragActive
            ? 'scale-[1.01] border-primary border-solid bg-primary/5 shadow-sm'
            : 'border-border bg-primary/[0.02] hover:border-primary/60 hover:bg-primary/[0.06] hover:shadow-sm',
        )}
      >
        <input {...getInputProps()} data-testid="file-upload-input" />
        <div
          className={cn(
            'flex items-center justify-center rounded-2xl transition-all duration-300',
            compact ? 'size-10' : 'size-12',
            isDragActive
              ? 'scale-110 bg-primary text-primary-foreground shadow-lg shadow-primary/30'
              : 'bg-background text-primary/50 shadow-sm group-hover:-translate-y-0.5 group-hover:bg-primary/5 group-hover:text-primary motion-reduce:group-hover:translate-y-0',
          )}
        >
          <Inbox className={cn(compact ? 'size-5' : 'size-6')} />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-semibold text-foreground">
            {isDragActive ? '释放以上传文件' : '点击或拖拽 PDF 文件到此区域'}
          </p>
          <p className="text-xs text-muted-foreground">仅支持单个 PDF 文件</p>
        </div>
      </div>

      {file && (
        <div className="animate-fade-in-up motion-reduce:animate-none flex items-center gap-3 rounded-xl border border-border bg-muted/50 px-4 py-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-emerald/10 text-emerald">
            <FileText className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-foreground">
              {file.name}
            </p>
            <p className="text-xs text-muted-foreground">
              {(file.size / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => onFile(null)}
            aria-label="移除文件"
            className="shrink-0 text-muted-foreground hover:text-destructive"
          >
            <X className="size-4" />
          </Button>
        </div>
      )}
    </div>
  );
}
