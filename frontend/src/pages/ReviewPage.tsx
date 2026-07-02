import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import dayjs from 'dayjs';
import {
  Loader2,
  AlertCircle,
  ChevronLeft,
  CheckCircle2,
  Bot,
  Send,
  ShieldCheck,
} from 'lucide-react';
import {
  useSubmission,
  useSubmissionStatus,
  useConversations,
  useChat,
  useFinalizeSubmission,
  isProcessing,
  isTerminal,
  type SubmissionDetail,
  type ConversationMessage,
  type AiSuggestion,
  type AiSuggestionDetail,
} from '@/api/submissions';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Empty } from '@/components/Empty';

const STATUS_LABEL: Record<string, string> = {
  pending: '排队等待处理',
  ocr_processing: 'OCR 识别中',
  ocr_done: 'OCR 已完成',
  agent_grading: 'AI 评分中',
  agent_reviewing: 'AI 复核中',
  agent_revising: 'AI 修订中',
  ready_for_review: '待审阅',
  reviewed: '已审阅',
  failed: '失败',
};

function ReviewContent({ data }: { data: SubmissionDetail }) {
  const navigate = useNavigate();
  const isReadOnly = data.status === 'reviewed';
  const isFailed = data.status === 'failed';

  // 左侧 OCR 切换:学生作业 / 作业题目
  const [ocrTab, setOcrTab] = useState<'submission' | 'question'>('submission');

  // 将 API 返回的 AISuggestion(字段可空)归一化为 AiSuggestion(字段非空),
  // 便于 AI 建议卡片与评分表单直接消费,无需每处都做 null 判断。
  const aiSuggestion: AiSuggestion | null = data.ai_suggestion
    ? {
        score: data.ai_suggestion.score ?? 0,
        max_score: data.ai_suggestion.max_score ?? 0,
        confidence: data.ai_suggestion.confidence ?? 0,
        feedback: data.ai_suggestion.feedback ?? '',
        details: (data.ai_suggestion.details ?? []).map((d) => ({
          criterion: d.criterion,
          score: d.score,
          max_score: d.max_score ?? 0,
          comment: d.comment,
          evidence: d.evidence,
        })),
      }
    : null;

  // 评分表单状态:reviewed 时取最终评分,其余取 AI 建议作为可编辑初值
  const initialSource = isReadOnly ? data.details : aiSuggestion?.details;
  const [details, setDetails] = useState<AiSuggestionDetail[]>(
    (initialSource ?? []).map((d) => ({
      criterion: d.criterion,
      score: d.score,
      max_score: d.max_score ?? 0,
      comment: d.comment,
      evidence: d.evidence,
    })),
  );
  const [feedback, setFeedback] = useState(
    isReadOnly ? (data.feedback ?? '') : (aiSuggestion?.feedback ?? ''),
  );
  const [reviewerName, setReviewerName] = useState(
    isReadOnly ? (data.reviewed_by ?? '') : '',
  );

  // 聊天面板
  const [chatInput, setChatInput] = useState('');
  const { data: conversations } = useConversations(data.id);
  const messages: ConversationMessage[] = conversations ?? [];
  const chatMutation = useChat(data.id);
  const finalizeMutation = useFinalizeSubmission(data.id);

  // 聊天消息列表自动滚动到底部
  const messagesEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length]);

  const handleSendMessage = () => {
    const message = chatInput.trim();
    if (!message || chatMutation.isPending) return;
    chatMutation.mutate(
      { message },
      {
        onSuccess: () => setChatInput(''),
        onError: () => toast.error('发送失败,请稍后重试'),
      },
    );
  };

  const handleFinalize = () => {
    if (!reviewerName.trim()) {
      toast.error('请输入审核教师姓名');
      return;
    }
    const totalScore = details.reduce((sum, d) => sum + (d.score || 0), 0);
    const totalMaxScore = details.reduce(
      (sum, d) => sum + (d.max_score || 0),
      0,
    );
    finalizeMutation.mutate(
      {
        reviewer_name: reviewerName.trim(),
        score: totalScore,
        max_score: totalMaxScore,
        feedback,
        details: details.map((d) => ({
          criterion: d.criterion,
          score: d.score,
          max_score: d.max_score,
          comment: d.comment,
          evidence: d.evidence ?? [],
        })),
      },
      {
        onSuccess: () => {
          toast.success('评分已提交');
          navigate(`/result/${data.id}`);
        },
        onError: () => toast.error('提交失败,请稍后重试'),
      },
    );
  };

  const ocrContent =
    ocrTab === 'submission' ? data.ocr_text : data.question_ocr_text;

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            协同评分
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            查看 AI 建议、与 AI 对话、确认最终评分
          </p>
        </div>
        <Button variant="outline" onClick={() => navigate('/history')}>
          <ChevronLeft className="size-4" />
          返回历史
        </Button>
      </div>

      {isFailed && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertTitle>AI 建议生成失败</AlertTitle>
          <AlertDescription>
            {data.error_message || 'AI 建议生成过程中发生错误,请手动填写评分'}
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col items-start gap-5 lg:flex-row">
        {/* 左侧:OCR 文本展示 */}
        <Card className="elevated-card flex w-full flex-col overflow-hidden lg:w-2/5">
          <CardHeader className="pb-4">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant={ocrTab === 'submission' ? 'default' : 'outline'}
                onClick={() => setOcrTab('submission')}
              >
                学生作业
              </Button>
              <Button
                size="sm"
                variant={ocrTab === 'question' ? 'default' : 'outline'}
                onClick={() => setOcrTab('question')}
              >
                作业题目
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex-1">
            <div className="max-h-[60vh] overflow-y-auto rounded-xl border border-border bg-muted/50 p-4 lg:max-h-[calc(100vh-220px)]">
              {ocrContent ? (
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                  {ocrContent}
                </p>
              ) : (
                <Empty
                  text={ocrTab === 'submission' ? '无 OCR 文本' : '无作业题目'}
                />
              )}
            </div>
          </CardContent>
        </Card>

        {/* 右侧:三块垂直堆叠 */}
        <div className="flex w-full flex-col gap-5 lg:w-3/5">
          {/* AI 建议卡片 */}
          <Card className="elevated-card overflow-hidden">
            <CardHeader className="pb-4">
              <CardTitle className="flex items-center gap-2 text-base">
                <Bot className="size-5 text-primary" />
                AI 建议评分
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {aiSuggestion ? (
                <>
                  <div className="flex items-center gap-4">
                    <div className="rounded-lg border bg-primary/[0.04] px-4 py-2 text-center">
                      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        建议总分
                      </p>
                      <p className="text-2xl font-bold text-primary">
                        {aiSuggestion.score}
                        <span className="text-sm font-medium text-muted-foreground">
                          /{aiSuggestion.max_score}
                        </span>
                      </p>
                    </div>
                    <div className="rounded-lg border bg-muted/40 px-3 py-2 text-sm">
                      置信度:
                      <strong className="ml-1">
                        {Math.round(aiSuggestion.confidence * 100)}%
                      </strong>
                    </div>
                  </div>
                  {aiSuggestion.feedback && (
                    <div className="rounded-xl border border-border bg-muted/50 p-4">
                      <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                        {aiSuggestion.feedback}
                      </p>
                    </div>
                  )}
                  {aiSuggestion.details.length > 0 && (
                    <div className="flex flex-col divide-y divide-border/60">
                      {aiSuggestion.details.map((item, index) => (
                        <div key={index} className="py-3 first:pt-0 last:pb-0">
                          <div className="flex items-start justify-between gap-4">
                            <p className="font-semibold text-foreground">
                              {item.criterion}
                            </p>
                            <span className="shrink-0 rounded-lg bg-primary/10 px-2.5 py-0.5 text-sm font-bold text-primary">
                              {item.score}/{item.max_score}
                            </span>
                          </div>
                          <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                            {item.comment}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <Empty text="AI 建议不可用" />
              )}
            </CardContent>
          </Card>

          {/* 聊天面板 */}
          <Card className="elevated-card overflow-hidden">
            <CardHeader className="pb-4">
              <CardTitle className="flex items-center gap-2 text-base">
                <Bot className="size-5 text-primary" />
                与 AI 对话
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="max-h-80 overflow-y-auto rounded-xl border border-border bg-muted/50 p-3">
                {messages.length > 0 ? (
                  <div className="flex flex-col gap-3">
                    {messages.map((msg) => (
                      <div
                        key={msg.id}
                        className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                      >
                        <div
                          className={`max-w-[80%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                            msg.role === 'user'
                              ? 'bg-primary text-primary-foreground'
                              : 'border bg-background'
                          }`}
                        >
                          {msg.content}
                        </div>
                      </div>
                    ))}
                    <div ref={messagesEndRef} />
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
                    <Bot className="size-8 opacity-40" />
                    <span className="text-sm">
                      开始与 AI 对话,询问评分理由或调整建议
                    </span>
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <Input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  placeholder="输入您的问题..."
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSendMessage();
                    }
                  }}
                  disabled={chatMutation.isPending}
                />
                <Button
                  onClick={handleSendMessage}
                  disabled={chatMutation.isPending || !chatInput.trim()}
                >
                  <Send className="size-4" />
                  发送
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* 教师最终评分表单 */}
          <Card className="elevated-card overflow-hidden">
            <CardHeader className="pb-4">
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldCheck className="size-5 text-primary" />
                最终评分
                {isReadOnly && (
                  <Badge variant="secondary" className="ml-1 gap-1">
                    <CheckCircle2 className="size-3" />
                    已审阅
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {details.length > 0 ? (
                <div className="flex flex-col gap-3">
                  {details.map((item, index) => (
                    <div
                      key={index}
                      className="space-y-2 rounded-xl border border-border p-3"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <Label className="font-semibold text-foreground">
                          {item.criterion}
                        </Label>
                        <div className="flex items-center gap-2">
                          <Input
                            type="number"
                            value={item.score}
                            disabled={isReadOnly}
                            onChange={(e) => {
                              const value = Number(e.target.value);
                              setDetails((prev) =>
                                prev.map((d, i) =>
                                  i === index ? { ...d, score: value } : d,
                                ),
                              );
                            }}
                            className="w-20"
                          />
                          <span className="text-sm text-muted-foreground">
                            / {item.max_score}
                          </span>
                        </div>
                      </div>
                      <Textarea
                        value={item.comment}
                        disabled={isReadOnly}
                        onChange={(e) => {
                          const value = e.target.value;
                          setDetails((prev) =>
                            prev.map((d, i) =>
                              i === index ? { ...d, comment: value } : d,
                            ),
                          );
                        }}
                        placeholder="评分说明..."
                        className="min-h-16"
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <Empty text="暂无评分项" />
              )}

              <div className="space-y-2">
                <Label>总体反馈</Label>
                <Textarea
                  value={feedback}
                  disabled={isReadOnly}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="总体反馈..."
                  className="min-h-20"
                />
              </div>

              <div className="space-y-2">
                <Label>审核教师</Label>
                <Input
                  value={reviewerName}
                  disabled={isReadOnly}
                  onChange={(e) => setReviewerName(e.target.value)}
                  placeholder="请输入姓名"
                />
              </div>

              {!isReadOnly && (
                <Button
                  onClick={handleFinalize}
                  disabled={finalizeMutation.isPending}
                  className="w-full"
                >
                  {finalizeMutation.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <ShieldCheck className="size-4" />
                  )}
                  提交最终评分
                </Button>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const rawNumericId = id !== undefined ? Number(id) : undefined;
  const numericId =
    rawNumericId !== undefined && !isNaN(rawNumericId) ? rawNumericId : undefined;

  // 轻量 status 先拉,处理中只靠 status 轮询;终态后再 enable 完整详情。
  // 避免处理中阶段拉取 ocr_text/ai_suggestion 等大字段(此时均为 null,属浪费)。
  const { data: statusData } = useSubmissionStatus(numericId);
  const detailEnabled = statusData ? isTerminal(statusData.status) : false;
  const { data, isLoading: isDetailLoading } = useSubmission(
    numericId,
    detailEnabled,
  );

  if (numericId === undefined) {
    return (
      <div className="mx-auto max-w-2xl">
        <Card className="elevated-card border-0">
          <CardContent className="flex flex-col items-center gap-4 py-16">
            <div className="flex size-14 items-center justify-center rounded-full bg-muted">
              <AlertCircle className="size-7 text-muted-foreground" />
            </div>
            <p className="text-muted-foreground">无效的记录 ID</p>
            <Button variant="outline" onClick={() => navigate('/history')}>
              返回历史记录
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  // status 首次拉取中
  if (!statusData) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">加载中...</p>
      </div>
    );
  }

  // 处理中:展示状态文字 + 上传时间 + 返回按钮
  if (isProcessing(statusData.status)) {
    return (
      <div className="mx-auto max-w-2xl">
        <div className="flex flex-col items-center justify-center gap-4 py-32">
          <Loader2 className="size-10 animate-spin text-primary" />
          <div className="text-center">
            <p className="text-base font-medium text-foreground">
              {STATUS_LABEL[statusData.status] ?? statusData.status}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              上传时间：
              {dayjs(statusData.uploaded_at).format('YYYY-MM-DD HH:mm:ss')}
            </p>
          </div>
          <Button variant="outline" onClick={() => navigate('/history')}>
            <ChevronLeft className="size-4" />
            返回历史
          </Button>
        </div>
      </div>
    );
  }

  // 终态:详情拉取中
  if (isDetailLoading || !data) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">加载评分详情中...</p>
      </div>
    );
  }

  // 终态(ready_for_review / reviewed / failed):展示协同评分布局
  return <ReviewContent data={data} />;
}
