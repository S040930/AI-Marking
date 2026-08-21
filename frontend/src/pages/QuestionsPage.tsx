import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Plus, Search, BookOpen } from 'lucide-react';
import { toast } from 'sonner';
import {
  type Question,
  useChangeQuestionConfigProfile,
  useDeleteQuestion,
  useQuestions,
  useRenameQuestion,
  useReplaceQuestion,
  useRetryQuestionOcr,
} from '@/api/questions';
import { useConfigProfiles } from '@/api/config';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { QuestionCard } from '@/components/questions/QuestionCard';
import {
  QuestionDangerDialog,
  type QuestionDanger,
} from '@/components/questions/QuestionDangerDialog';
import { QuestionPromptDialog } from '@/components/questions/QuestionPromptDialog';
import { errorMessage } from '@/lib/questionErrors';
import { useLanguage } from '@/i18n';

export default function QuestionsPage() {
  const [search, setSearch] = useState('');
  const [danger, setDanger] = useState<QuestionDanger | null>(null);
  const [confirmation, setConfirmation] = useState('');
  const { t } = useLanguage();
  const [acknowledgedDeletion, setAcknowledgedDeletion] = useState(false);
  const [promptQuestion, setPromptQuestion] = useState<Question | null>(null);
  const switchProfileMutation = useChangeQuestionConfigProfile();
  const { data: profiles } = useConfigProfiles();
  const renameMutation = useRenameQuestion();
  const retryMutation = useRetryQuestionOcr();
  const deleteMutation = useDeleteQuestion();
  const replaceMutation = useReplaceQuestion();
  const { data, isLoading } = useQuestions(search);

  const rename = (question: Question) => {
    const name = window.prompt(t('输入新的题目名称'), question.name)?.trim();
    if (!name || name === question.name) return;
    renameMutation.mutate(
      { id: question.id, name },
      {
        onSuccess: () => toast.success(t('题目名称已更新')),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  const retryUpload = (id: number, file: File) => {
    retryMutation.mutate(
      { id, file },
      {
        onSuccess: () => toast.success(t('文件已重新上传，正在识别')),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  const switchProfile = (id: number, configProfileId: number) => {
    switchProfileMutation.mutate(
      { id, configProfileId },
      {
        onSuccess: () => toast.success(t('配置项目已更新')),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  const resetDangerState = () => {
    setDanger(null);
    setConfirmation('');
    setAcknowledgedDeletion(false);
  };

  const confirmDanger = () => {
    if (!danger || confirmation !== danger.question.name) return;
    if (danger.type === 'delete') {
      deleteMutation.mutate(
        { id: danger.question.id, confirmationName: confirmation },
        {
          onSuccess: (result) => {
            toast.success(
              `${t('题目已删除，清理了')} ${result.deleted_submission_count} ${t('条批改记录')}`,
            );
            resetDangerState();
          },
          onError: (error) => toast.error(errorMessage(error)),
        },
      );
    } else if (danger.file) {
      replaceMutation.mutate(
        {
          id: danger.question.id,
          file: danger.file,
          confirmationName: confirmation,
          acknowledgeDeletion: acknowledgedDeletion,
        },
        {
          onSuccess: (result) => {
            toast.success(
              `${t('新版已进入后台识别，成功后将清理')} ${result.affected_submission_count} ${t('条旧批改记录')}`,
            );
            resetDangerState();
          },
          onError: (error) => toast.error(errorMessage(error)),
        },
      );
    }
  };

  const isDangerPending = deleteMutation.isPending || replaceMutation.isPending;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t('题目库')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('题目只需上传并识别一次，之后可直接用于多份学生作业。')}
          </p>
        </div>
        <Button asChild>
          <Link to="/questions/upload">
            <Plus />
            {t('上传题目')}
          </Link>
        </Button>
      </div>

      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t('搜索题目名称或文件名')}
          className="pl-9"
        />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="animate-spin text-primary" />
        </div>
      ) : data?.items.length ? (
        <div className="grid gap-4 md:grid-cols-2">
          {data.items.map((question) => (
            <QuestionCard
              key={question.id}
              question={question}
              profiles={profiles}
              isSwitchingProfile={switchProfileMutation.isPending}
              onRename={rename}
              onRetryUpload={retryUpload}
              onSwitchProfile={switchProfile}
              onReplace={(q, file) => setDanger({ type: 'replace', question: q, file })}
              onDelete={(q) => setDanger({ type: 'delete', question: q })}
              onCopyPrompt={setPromptQuestion}
            />
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed py-20 text-center">
          <BookOpen className="mx-auto mb-3 size-10 text-muted-foreground/50" />
          <p className="font-medium">{t('还没有可显示的题目')}</p>
          <p className="mt-1 text-sm text-muted-foreground">{t('上传第一份 PDF 题目开始使用')}</p>
        </div>
      )}

      <QuestionDangerDialog
        danger={danger}
        confirmation={confirmation}
        acknowledgedDeletion={acknowledgedDeletion}
        isPending={isDangerPending}
        onConfirmationChange={setConfirmation}
        onAcknowledgedDeletionChange={setAcknowledgedDeletion}
        onConfirm={confirmDanger}
        onOpenChange={(open) => {
          if (!open && !isDangerPending) resetDangerState();
        }}
      />

      <QuestionPromptDialog
        question={promptQuestion}
        onOpenChange={(open) => {
          if (!open) setPromptQuestion(null);
        }}
      />
    </div>
  );
}
