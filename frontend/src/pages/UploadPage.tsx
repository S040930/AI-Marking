import { useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Check, FileText, Inbox, Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { useUploadSubmission } from '@/api/submissions';
import { cn } from '@/lib/utils';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const navigate = useNavigate();
  const uploadMutation = useUploadSubmission();

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { 'application/pdf': ['.pdf'] },
    maxFiles: 1,
    multiple: false,
    onDrop: (acceptedFiles) => {
      if (acceptedFiles.length > 0) {
        setFile(acceptedFiles[0]);
      }
    },
    onDropRejected: () => {
      toast.error('仅支持 PDF 文件');
    },
  });

  const handleSubmit = () => {
    if (!file) return;
    uploadMutation.mutate(file, {
      onSuccess: (data) => {
        toast.success('上传成功，正在批改');
        navigate(`/result/${data.id}`);
      },
      onError: (err) => {
        toast.error(err.message || '上传失败');
      },
    });
  };

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          上传作业
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          支持 PDF 格式，AI 将自动完成 OCR 识别与智能评分。
        </p>
      </div>

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Check className="size-5" />
            </div>
            <div>
              <CardTitle className="text-lg">开始新的批改</CardTitle>
              <CardDescription>
                拖拽文件到下方区域，或点击选择本地 PDF
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-5 pt-4">
          <div
            {...getRootProps()}
            className={cn(
              'group relative flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-14 text-center transition-all duration-300',
              isDragActive
                ? 'border-primary bg-primary/5'
                : 'border-border bg-primary/[0.03] hover:border-primary/40 hover:bg-primary/[0.06]',
            )}
          >
            <input {...getInputProps()} />
            <div
              className={cn(
                'flex size-16 items-center justify-center rounded-2xl transition-all duration-300',
                isDragActive
                  ? 'bg-primary text-primary-foreground shadow-lg shadow-primary/30 scale-110'
                  : 'bg-background text-primary/50 shadow-sm group-hover:bg-primary/5 group-hover:text-primary',
              )}
            >
              <Inbox className="size-8" />
            </div>
            <div className="space-y-1">
              <p className="text-sm font-semibold text-foreground">
                点击或拖拽 PDF 文件到此区域
              </p>
              <p className="text-xs text-muted-foreground">
                仅支持单个 PDF 文件，大小建议不超过 20MB
              </p>
            </div>
          </div>

          {file && (
            <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/50 px-4 py-3">
              <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <FileText className="size-5" />
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
                onClick={() => setFile(null)}
                aria-label="移除文件"
                className="shrink-0 text-muted-foreground hover:text-destructive"
              >
                <X className="size-4" />
              </Button>
            </div>
          )}

          <Button
            size="lg"
            className="w-full text-base font-semibold"
            disabled={!file}
            onClick={handleSubmit}
          >
            {uploadMutation.isPending ? (
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
        </CardContent>
      </Card>
    </div>
  );
}
