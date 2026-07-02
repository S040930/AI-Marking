import { useState } from 'react';
import { useDropzone } from 'react-dropzone';
import axios, { type AxiosError } from 'axios';
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

interface FileSlotProps {
  label: string;
  description: string;
  file: File | null;
  onFile: (file: File | null) => void;
}

function FileSlot({ label, description, file, onFile }: FileSlotProps) {
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { 'application/pdf': ['.pdf'] },
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
          'group relative flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-all duration-300',
          isDragActive
            ? 'scale-[1.01] border-primary border-solid bg-primary/5 shadow-sm'
            : 'border-border bg-primary/[0.02] hover:border-primary/60 hover:bg-primary/[0.06] hover:shadow-sm',
        )}
      >
        <input {...getInputProps()} />
        <div
          className={cn(
            'flex size-12 items-center justify-center rounded-2xl transition-all duration-300',
            isDragActive
              ? 'scale-110 bg-primary text-primary-foreground shadow-lg shadow-primary/30'
              : 'bg-background text-primary/50 shadow-sm group-hover:-translate-y-0.5 group-hover:bg-primary/5 group-hover:text-primary motion-reduce:group-hover:translate-y-0',
          )}
        >
          <Inbox className="size-6" />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-semibold text-foreground">
            {isDragActive ? '释放以上传 PDF' : '点击或拖拽 PDF 文件到此区域'}
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

export default function UploadPage() {
  const [questionFile, setQuestionFile] = useState<File | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const navigate = useNavigate();
  const uploadMutation = useUploadSubmission();

  const handleSubmit = () => {
    if (!file || !questionFile) return;
    uploadMutation.mutate(
      { file, questionFile },
      {
        onSuccess: (data) => {
          toast.success('上传成功，正在批改');
          navigate(`/result/${data.id}`);
        },
        onError: (err) => {
          let message = err.message || '上传失败';
          if (axios.isAxiosError(err)) {
            const axiosErr = err as AxiosError<{ detail?: string }>;
            message = axiosErr.response?.data?.detail ?? message;
          }
          toast.error(message);
        },
      },
    );
  };

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          上传作业
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          上传作业题目与学生作业两份 PDF，AI 将自动完成 OCR 识别与智能评分。
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
                分别上传作业题目与学生作业，各一份 PDF
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-4">
          <FileSlot
            label="作业题目 PDF"
            description="本次作业的题目要求，作为评分依据"
            file={questionFile}
            onFile={setQuestionFile}
          />
          <FileSlot
            label="学生作业 PDF"
            description="学生提交的作业内容"
            file={file}
            onFile={setFile}
          />
          <Button
            size="lg"
            className="btn-press w-full text-base font-semibold disabled:cursor-not-allowed disabled:opacity-70"
            disabled={!file || !questionFile}
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
