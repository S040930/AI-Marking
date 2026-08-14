import { useState } from 'react';
import { BookOpen, Loader2, Plus, Search } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { FileSlot } from '@/components/upload/FileSlot';
import { useCreateQuestion, useQuestions, type Question } from '@/api/questions';

interface QuestionSelectorProps {
  selectedQuestionId: number | null;
  onSelectQuestion: (id: number | null) => void;
  onQuestionCreated: (question: Question) => void;
}

export function QuestionSelector({
  selectedQuestionId,
  onSelectQuestion,
  onQuestionCreated,
}: QuestionSelectorProps) {
  const [mode, setMode] = useState<'existing' | 'new'>('existing');
  const [questionSearch, setQuestionSearch] = useState('');
  const [newQuestionFile, setNewQuestionFile] = useState<File | null>(null);
  const [newQuestionName, setNewQuestionName] = useState('');

  const { data: questionsData } = useQuestions(questionSearch, 20);
  const createQuestionMutation = useCreateQuestion();

  const selectedQuestion = questionsData?.items.find(
    (question) => question.id === selectedQuestionId,
  );

  const handleCreateQuestion = () => {
    if (!newQuestionFile) return;
    createQuestionMutation.mutate(
      { file: newQuestionFile, name: newQuestionName.trim() || undefined },
      {
        onSuccess: (question) => {
          setNewQuestionFile(null);
          setNewQuestionName('');
          setMode('existing');
          onQuestionCreated(question);
          toast.success('题目已上传，OCR 完成后即可开始批改');
        },
        onError: (error) => {
          toast.error(error.message || '题目上传失败');
        },
      },
    );
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Button
          type="button"
          variant={mode === 'existing' ? 'secondary' : 'ghost'}
          onClick={() => {
            setMode('existing');
            setNewQuestionFile(null);
            setNewQuestionName('');
          }}
        >
          <BookOpen />选择已有题目
        </Button>
        <Button
          type="button"
          variant={mode === 'new' ? 'secondary' : 'ghost'}
          onClick={() => {
            setMode('new');
            onSelectQuestion(null);
          }}
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
              questionsData.items.map((question) => {
                const replacementActive =
                  question.replacement_status === 'pending' ||
                  question.replacement_status === 'processing';
                const disabled = question.status !== 'ready' || replacementActive;
                return (
                  <button
                    key={question.id}
                    type="button"
                    disabled={disabled}
                    onClick={() => onSelectQuestion(question.id)}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-lg border px-3 py-3 text-left transition-colors',
                      selectedQuestionId === question.id
                        ? 'border-primary bg-primary/5'
                        : 'border-transparent hover:bg-muted',
                      disabled && 'cursor-not-allowed opacity-55',
                    )}
                  >
                    <BookOpen className="size-4 shrink-0 text-primary" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {question.name}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {question.original_filename} · {question.submission_count} 份记录
                      </span>
                    </span>
                    <Badge
                      variant={question.status === 'failed' ? 'destructive' : 'secondary'}
                    >
                      {replacementActive
                        ? question.replacement_status === 'pending'
                          ? '新版排队中'
                          : '新版识别中'
                        : question.replacement_status === 'failed'
                          ? '旧版可用'
                          : question.status === 'ready'
                            ? '可使用'
                            : question.status === 'failed'
                              ? '识别失败'
                              : '识别中'}
                    </Badge>
                  </button>
                );
              })
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
            label="新题目文件"
            description="上传后系统会自动 OCR，之后可重复使用"
            file={newQuestionFile}
            onFile={setNewQuestionFile}
            compact
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
        </div>
      )}

      {selectedQuestion && selectedQuestion.status !== 'ready' && (
        <p className="text-center text-xs text-muted-foreground">
          {selectedQuestion.status === 'failed'
            ? `OCR 失败：${selectedQuestion.error_message ?? '请到题目库重试'}`
            : '题目正在识别，完成后会自动选中…'}
        </p>
      )}
    </div>
  );
}
