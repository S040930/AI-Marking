import { useEffect, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import axios, { type AxiosError } from 'axios';
import { BookOpen, Check, FileText, Inbox, Loader2, Plus, Search, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { useUploadSubmission } from '@/api/submissions';
import { useCreateQuestion, useQuestions } from '@/api/questions';
import { cn } from '@/lib/utils';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';

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
  const [mode, setMode] = useState<'existing' | 'new'>('existing');
  const [questionSearch, setQuestionSearch] = useState('');
  const [selectedQuestionId, setSelectedQuestionId] = useState<number | null>(null);
  const [newQuestionFile, setNewQuestionFile] = useState<File | null>(null);
  const [newQuestionName, setNewQuestionName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const navigate = useNavigate();
  const uploadMutation = useUploadSubmission();
  const createQuestionMutation = useCreateQuestion();
  const { data: questionsData } = useQuestions(questionSearch, 20);
  const selectedQuestion = questionsData?.items.find(
    (question) => question.id === selectedQuestionId,
  );

  useEffect(() => {
    if (selectedQuestion?.status === 'ready' && mode === 'new') {
      setMode('existing');
      toast.success('题目识别完成，已自动选中');
    }
  }, [mode, selectedQuestion?.status]);

  const handleSubmit = () => {
    if (!file || !selectedQuestionId || selectedQuestion?.status !== 'ready') return;
    uploadMutation.mutate(
      { file, questionId: selectedQuestionId },
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

  const handleCreateQuestion = () => {
    if (!newQuestionFile) return;
    createQuestionMutation.mutate(
      { file: newQuestionFile, name: newQuestionName.trim() || undefined },
      {
        onSuccess: (question) => {
          setSelectedQuestionId(question.id);
          setQuestionSearch('');
          toast.success('题目已上传，OCR 完成后即可开始批改');
        },
        onError: (error) => toast.error(error.message || '题目上传失败'),
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
          从题目库选择评分依据，再上传一份学生作业 PDF。
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
                已有题目无需重复上传和识别
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-4">
          <div className="space-y-3">
            <div>
              <p className="text-sm font-semibold">选择作业题目</p>
              <p className="text-xs text-muted-foreground">每份作业关联一个题目</p>
            </div>
            <div className="grid grid-cols-2 rounded-xl bg-muted p-1">
              <Button
                type="button"
                variant={mode === 'existing' ? 'secondary' : 'ghost'}
                onClick={() => setMode('existing')}
              >
                <BookOpen />选择已有题目
              </Button>
              <Button
                type="button"
                variant={mode === 'new' ? 'secondary' : 'ghost'}
                onClick={() => setMode('new')}
              >
                <Plus />上传新题目
              </Button>
            </div>

            {mode === 'existing' ? (
              <div className="space-y-3">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={questionSearch}
                    onChange={(event) => setQuestionSearch(event.target.value)}
                    placeholder="搜索题目"
                    className="pl-9"
                  />
                </div>
                <div className="max-h-64 space-y-2 overflow-y-auto rounded-xl border p-2">
                  {questionsData?.items.length ? (
                    questionsData.items.map((question) => (
                      <button
                        key={question.id}
                        type="button"
                        disabled={question.status !== 'ready'}
                        onClick={() => setSelectedQuestionId(question.id)}
                        className={cn(
                          'flex w-full items-center gap-3 rounded-lg border px-3 py-3 text-left transition-colors',
                          selectedQuestionId === question.id
                            ? 'border-primary bg-primary/5'
                            : 'border-transparent hover:bg-muted',
                          question.status !== 'ready' && 'cursor-not-allowed opacity-55',
                        )}
                      >
                        <BookOpen className="size-4 shrink-0 text-primary" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{question.name}</span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {question.original_filename} · {question.submission_count} 份记录
                          </span>
                        </span>
                        <Badge variant={question.status === 'ready' ? 'secondary' : question.status === 'failed' ? 'destructive' : 'outline'}>
                          {question.status === 'ready'
                            ? '可使用'
                            : question.status === 'failed'
                              ? '识别失败'
                              : '识别中'}
                        </Badge>
                      </button>
                    ))
                  ) : (
                    <p className="py-8 text-center text-sm text-muted-foreground">
                      没有匹配的题目，请上传新题目
                    </p>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-3 rounded-xl border p-4">
                <Input
                  value={newQuestionName}
                  onChange={(event) => setNewQuestionName(event.target.value)}
                  placeholder="题目名称（可选，默认使用文件名）"
                />
                <FileSlot
                  label="新题目 PDF"
                  description="上传后系统会自动 OCR，之后可重复使用"
                  file={newQuestionFile}
                  onFile={setNewQuestionFile}
                />
                <Button
                  type="button"
                  variant="outline"
                  className="w-full"
                  disabled={!newQuestionFile || createQuestionMutation.isPending}
                  onClick={handleCreateQuestion}
                >
                  {createQuestionMutation.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Plus />
                  )}
                  添加到题目库
                </Button>
                {selectedQuestion && selectedQuestion.status !== 'ready' && (
                  <p className="text-center text-xs text-muted-foreground">
                    {selectedQuestion.status === 'failed'
                      ? `OCR 失败：${selectedQuestion.error_message ?? '请到题目库重试'}`
                      : '题目正在识别，完成后会自动选中…'}
                  </p>
                )}
              </div>
            )}
          </div>
          <FileSlot
            label="学生作业 PDF"
            description="学生提交的作业内容"
            file={file}
            onFile={setFile}
          />
          <Button
            size="lg"
            className="btn-press w-full text-base font-semibold disabled:cursor-not-allowed disabled:opacity-70"
            disabled={
              !file ||
              !selectedQuestionId ||
              selectedQuestion?.status !== 'ready' ||
              uploadMutation.isPending
            }
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
