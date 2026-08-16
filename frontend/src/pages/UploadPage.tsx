import { useState } from 'react';
import axios, { type AxiosError } from 'axios';
import { Check, ChevronLeft, ChevronRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { useUploadSubmission } from '@/api/submissions';
import { useQuestions, type Question } from '@/api/questions';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Stepper } from '@/components/ui/stepper';
import { QuestionSelector } from '@/components/upload/QuestionSelector';
import { StudentFileUploader } from '@/components/upload/StudentFileUploader';
import { SubmissionSummary } from '@/components/upload/SubmissionSummary';

const STEPS = ['选择题目', '上传作业', '确认提交'];

export default function UploadPage() {
  const [currentStep, setCurrentStep] = useState<1 | 2 | 3>(1);
  const [selectedQuestionId, setSelectedQuestionId] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);

  const navigate = useNavigate();
  const uploadMutation = useUploadSubmission();
  const { data: questionsData } = useQuestions('', 20);

  const selectedQuestion = questionsData?.items.find(
    (question) => question.id === selectedQuestionId,
  );

  const selectedQuestionFrozen =
    selectedQuestion?.replacement_status === 'pending' ||
    selectedQuestion?.replacement_status === 'processing';

  const canProceedToStep2 =
    !!selectedQuestion &&
    selectedQuestion.status === 'ready' &&
    !selectedQuestionFrozen;

  const canProceedToStep3 = !!file;

  const handleQuestionCreated = (question: Question) => {
    setSelectedQuestionId(question.id);
  };

  const handleNext = () => {
    if (currentStep === 1 && canProceedToStep2) {
      setCurrentStep(2);
    } else if (currentStep === 2 && canProceedToStep3) {
      setCurrentStep(3);
    }
  };

  const handleBack = () => {
    if (currentStep === 2) {
      setCurrentStep(1);
    } else if (currentStep === 3) {
      setCurrentStep(2);
    }
  };

  const handleSubmit = () => {
    if (
      !file ||
      !selectedQuestionId ||
      selectedQuestion?.status !== 'ready' ||
      selectedQuestionFrozen
    )
      return;

    uploadMutation.mutate(
      { file, questionId: selectedQuestionId },
      {
        onSuccess: (data) => {
          toast.success('上传成功，等待 MCP 客户端评分');
          navigate(`/review/${data.id}`);
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
          上传学生 PDF 后，编程助手将通过本地 MCP 接口完成评分，最终成绩由你在网页确认。
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
              <CardDescription>已有题目无需重复上传和识别</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-6 pt-2">
          <Stepper steps={STEPS} currentStep={currentStep} />

          <div className="min-h-[240px] animate-stepper-enter motion-reduce:animate-none">
            {currentStep === 1 && (
              <QuestionSelector
                selectedQuestionId={selectedQuestionId}
                onSelectQuestion={setSelectedQuestionId}
                onQuestionCreated={handleQuestionCreated}
              />
            )}

            {currentStep === 2 && (
              <StudentFileUploader
                file={file}
                onFileChange={setFile}
                questionName={selectedQuestion?.name}
              />
            )}

            {currentStep === 3 && selectedQuestion && file && (
              <SubmissionSummary
                questionName={selectedQuestion.name}
                fileName={file.name}
                fileSize={file.size / 1024 / 1024}
                onSubmit={handleSubmit}
                onBack={handleBack}
                isSubmitting={uploadMutation.isPending}
                canSubmit={canProceedToStep2 && canProceedToStep3}
              />
            )}
          </div>

          {currentStep !== 3 && (
            <div className="flex flex-col-reverse gap-3 border-t pt-5 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="outline"
                className="w-full sm:w-auto"
                onClick={handleBack}
                disabled={currentStep === 1}
              >
                <ChevronLeft />
                上一步
              </Button>
              <Button
                type="button"
                className="btn-press w-full sm:w-auto"
                onClick={handleNext}
                disabled={
                  (currentStep === 1 && !canProceedToStep2) ||
                  (currentStep === 2 && !canProceedToStep3)
                }
              >
                下一步
                <ChevronRight />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
