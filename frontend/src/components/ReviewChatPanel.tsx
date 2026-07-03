import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import {
  Bot,
  Send,
  User,
  CheckCircle2,
  Loader2,
  Sparkles,
  RotateCcw,
} from 'lucide-react';
import {
  useConversations,
  useChat,
  type AiSuggestion,
  type FinalizePayload,
  type SuggestionSnapshot,
  type ConversationMessage,
} from '@/api/submissions';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Empty } from '@/components/Empty';

interface ReviewChatPanelProps {
  submissionId: number;
  initialSuggestion: AiSuggestion | null;
  isReadOnly: boolean;
  reviewerName: string;
  onFinalize: (payload: FinalizePayload) => void;
  className?: string;
}

function normalizeSuggestion(suggestion: AiSuggestion | SuggestionSnapshot | null) {
  if (!suggestion) return null;
  return {
    score: suggestion.score,
    max_score: suggestion.max_score,
    confidence: 'confidence' in suggestion ? suggestion.confidence : 0,
    feedback: suggestion.feedback,
    details: suggestion.details.map((d) => ({
      criterion: d.criterion,
      score: d.score,
      max_score: d.max_score,
      comment: d.comment,
      evidence: d.evidence ?? [],
    })),
  };
}

function ScoreBar({ score, maxScore }: { score: number; maxScore: number }) {
  const pct = maxScore > 0 ? (score / maxScore) * 100 : 0;
  return (
    <div className="score-bar-track h-1.5 w-full">
      <div
        className="score-bar-fill h-full"
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

function ScoreInsightCard({
  suggestion,
  isInitial = false,
}: {
  suggestion: SuggestionSnapshot;
  isInitial?: boolean;
}) {
  return (
    <div className="animate-score-card-enter w-full rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            {isInitial ? 'AI 初步评分' : 'AI 建议评分'}
          </p>
          <p className="mt-0.5 text-2xl font-bold tracking-tight text-foreground">
            {suggestion.score}
            <span className="ml-0.5 text-base font-medium text-muted-foreground">
              /{suggestion.max_score}
            </span>
          </p>
        </div>
        <Badge
          variant="secondary"
          className="h-5 shrink-0 gap-1 px-2 text-[10px] font-medium"
        >
          <Sparkles className="size-3" />
          置信度 {Math.round(suggestion.confidence * 100)}%
        </Badge>
      </div>

      {suggestion.feedback && (
        <div className="mb-4 rounded-lg bg-slate-50 px-3 py-2.5">
          <p className="text-xs leading-relaxed text-slate-600">
            {suggestion.feedback}
          </p>
        </div>
      )}

      <div className="space-y-4">
        {suggestion.details.map((item, idx) => (
          <div
            key={idx}
            className="space-y-2 border-b border-slate-100 pb-4 last:border-b-0 last:pb-0"
          >
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-foreground">
                {item.criterion}
              </span>
              <span className="tabular-nums text-muted-foreground">
                {item.score}/{item.max_score}
              </span>
            </div>
            <ScoreBar score={item.score} maxScore={item.max_score} />
            {item.comment && (
              <p className="text-[11px] leading-relaxed text-slate-600">
                {item.comment}
              </p>
            )}
            {(item.evidence?.length ?? 0) > 0 && (
              <div className="rounded-md border border-blue-100 bg-blue-50/70 px-2.5 py-2">
                <p className="mb-1 text-[10px] font-medium text-blue-700">
                  原文证据
                </p>
                <ul className="space-y-1 text-[10px] leading-relaxed text-slate-600">
                  {item.evidence?.map((evidence, evidenceIndex) => (
                    <li key={evidenceIndex}>“{evidence}”</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  suggestion,
  index,
}: {
  message: ConversationMessage;
  suggestion: SuggestionSnapshot | AiSuggestion | null;
  index: number;
}) {
  const isUser = message.role === 'user';
  const staggerClass = `stagger-msg-${Math.min(index + 1, 5)}`;
  const normalized = normalizeSuggestion(suggestion);

  return (
    <div
      className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''} animate-message-enter ${staggerClass}`}
    >
      <div
        className={`flex size-8 shrink-0 items-center justify-center rounded-full border ${
          isUser
            ? 'border-transparent bg-primary text-primary-foreground'
            : 'border-border bg-muted text-primary'
        }`}
      >
        {isUser ? <User className="size-4" /> : <Bot className="size-4" />}
      </div>
      <div
        className={`flex max-w-[85%] flex-col gap-2 ${
          isUser ? 'items-end' : 'items-start'
        }`}
      >
        <div
          className={`whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
            isUser
              ? 'message-bubble-user rounded-br-md bg-primary text-primary-foreground'
              : 'message-bubble-ai rounded-bl-md border border-border bg-card'
          }`}
        >
          {message.content}
        </div>
        {!isUser && normalized && <ScoreInsightCard suggestion={normalized} />}
      </div>
    </div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex gap-3 animate-message-enter stagger-msg-1">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-muted text-primary">
        <Bot className="size-4" />
      </div>
      <div className="message-bubble-ai flex items-center gap-2 rounded-2xl rounded-bl-md border border-border bg-card px-4 py-2.5 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        AI 思考中…
      </div>
    </div>
  );
}

function FinalizeConfirmCard({
  suggestion,
  onConfirm,
  onContinue,
}: {
  suggestion: SuggestionSnapshot;
  onConfirm: () => void;
  onContinue: () => void;
}) {
  return (
    <div className="animate-score-card-enter w-full max-w-sm rounded-xl border border-primary/20 bg-primary/[0.04] p-4">
      <div className="mb-2 flex items-center gap-2">
        <div className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-primary">
          <CheckCircle2 className="size-4" />
        </div>
        <h4 className="text-sm font-semibold text-foreground">最终评分确认</h4>
      </div>
      <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
        AI 已根据你的反馈校准出最终评分，请确认是否提交。
      </p>
      <ScoreInsightCard suggestion={suggestion} />
      <div className="mt-3 flex items-center gap-2">
        <Button
          size="sm"
          onClick={onConfirm}
          className="rounded-full text-xs active:scale-[0.98]"
        >
          <CheckCircle2 className="mr-1 size-3.5" />
          确认提交
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onContinue}
          className="rounded-full text-xs text-muted-foreground hover:text-foreground"
        >
          继续讨论
        </Button>
      </div>
    </div>
  );
}

export default function ReviewChatPanel({
  submissionId,
  initialSuggestion,
  isReadOnly,
  reviewerName,
  onFinalize,
  className,
}: ReviewChatPanelProps) {
  const { data: conversations } = useConversations(submissionId);
  const chatMutation = useChat(submissionId);
  const [chatInput, setChatInput] = useState('');
  const [workingSuggestion, setWorkingSuggestion] = useState<SuggestionSnapshot | null>(
    normalizeSuggestion(initialSuggestion),
  );
  const [pendingFinalize, setPendingFinalize] = useState<SuggestionSnapshot | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const previousMessageCountRef = useRef<number | null>(null);

  useEffect(() => {
    if (!conversations) return;
    if (previousMessageCountRef.current === null) {
      previousMessageCountRef.current = conversations.length;
      return;
    }
    if (conversations.length > previousMessageCountRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
    previousMessageCountRef.current = conversations.length;
  }, [conversations]);

  useEffect(() => {
    if (chatMutation.isPending || pendingFinalize) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMutation.isPending, pendingFinalize]);

  useEffect(() => {
    if (initialSuggestion) {
      setWorkingSuggestion(normalizeSuggestion(initialSuggestion));
    }
  }, [initialSuggestion]);

  const handleSendMessage = () => {
    const message = chatInput.trim();
    if (!message || chatMutation.isPending || isReadOnly) return;

    chatMutation.mutate(
      { message, reviewer_name: reviewerName },
      {
        onSuccess: (data) => {
          setChatInput('');
          if (data.suggestion) {
            setWorkingSuggestion(data.suggestion);
          }
          if (data.action === 'finalize' && data.suggestion) {
            setPendingFinalize(data.suggestion);
          }
        },
        onError: () => toast.error('发送失败，请稍后重试'),
      },
    );
  };

  const handleAdoptAndSubmit = () => {
    if (isReadOnly || !workingSuggestion) return;

    const payload: FinalizePayload = {
      reviewer_name: reviewerName,
      score: workingSuggestion.score,
      max_score: workingSuggestion.max_score,
      feedback: workingSuggestion.feedback,
      details: workingSuggestion.details.map((d) => ({
        criterion: d.criterion,
        score: d.score,
        max_score: d.max_score,
        comment: d.comment,
        evidence: d.evidence ?? [],
      })),
    };
    onFinalize(payload);
  };

  const handleConfirmFinalize = () => {
    if (!pendingFinalize) return;

    const payload: FinalizePayload = {
      reviewer_name: reviewerName,
      score: pendingFinalize.score,
      max_score: pendingFinalize.max_score,
      feedback: pendingFinalize.feedback,
      details: pendingFinalize.details.map((d) => ({
        criterion: d.criterion,
        score: d.score,
        max_score: d.max_score,
        comment: d.comment,
        evidence: d.evidence ?? [],
      })),
    };
    onFinalize(payload);
    setPendingFinalize(null);
  };

  const messages = conversations ?? [];
  const showEmpty = messages.length === 0 && !initialSuggestion;

  return (
    <div
      className={`flex h-full flex-col overflow-hidden ${className ?? ''}`}
    >
      {/* Messages */}
      <div className="flex-1 overflow-y-auto bg-slate-50/90 p-5">
        <div className="flex flex-col gap-5">
          {initialSuggestion && (
            <ScoreInsightCard
              suggestion={normalizeSuggestion(initialSuggestion)!}
              isInitial
            />
          )}

          {messages.map((msg, idx) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              suggestion={null}
              index={idx}
            />
          ))}

          {showEmpty && (
            <div className="py-8">
              <Empty text="暂无对话，开始与 AI 讨论评分吧" />
            </div>
          )}

          {chatMutation.isPending && <ThinkingIndicator />}

          {pendingFinalize && (
            <FinalizeConfirmCard
              suggestion={pendingFinalize}
              onConfirm={handleConfirmFinalize}
              onContinue={() => setPendingFinalize(null)}
            />
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-slate-200 bg-white/95 p-4 shadow-[0_-4px_16px_rgba(15,23,42,0.04)] backdrop-blur-sm">
        {!isReadOnly && workingSuggestion && (
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={handleAdoptAndSubmit}
              disabled={chatMutation.isPending}
              className="h-8 gap-1 rounded-full px-3 text-xs shadow-sm active:scale-[0.98]"
            >
              <CheckCircle2 className="size-3.5" />
              确认最终评分
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                setChatInput(
                  '请重新复核这份作业，重点关注评分标准是否执行过严或过松，并给出调整后的建议分数与理由。',
                )
              }
              disabled={chatMutation.isPending}
              className="h-7 gap-1 rounded-full px-3 text-xs text-muted-foreground hover:bg-muted hover:text-foreground active:scale-[0.98]"
            >
              <RotateCcw className="size-3.5" />
              要求 AI 复核
            </Button>
          </div>
        )}

        {isReadOnly ? (
          <div className="flex items-center justify-center gap-2 rounded-xl border border-border bg-muted/40 py-3 text-sm text-muted-foreground">
            <CheckCircle2 className="size-4" />
            该作业已审阅，对话已锁定
          </div>
        ) : (
          <div className="flex gap-2">
            <Textarea
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="输入评分调整或理由，例如：观点发展给 5 分，因为论据更充分…"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSendMessage();
                }
              }}
              disabled={chatMutation.isPending}
              className="min-h-[52px] resize-none rounded-2xl border-border bg-background px-4 py-3 text-sm leading-relaxed shadow-sm transition-shadow focus:border-primary/30 focus:ring-1 focus:ring-primary/20"
              rows={1}
            />
            <Button
              onClick={handleSendMessage}
              disabled={chatMutation.isPending || !chatInput.trim()}
              className="size-[52px] shrink-0 self-end rounded-full active:scale-[0.96]"
            >
              {chatMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
