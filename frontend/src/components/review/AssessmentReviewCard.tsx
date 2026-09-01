import { useState } from 'react';
import { toast } from 'sonner';
import dayjs from 'dayjs';
import { Copy, Check } from 'lucide-react';
import type { AssessmentReview, SubmissionDetail } from '@/api/submissions';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useLanguage } from '@/i18n';
import { CLIENT_LABELS } from '@/lib/mcpClients';

const REVIEW_VERDICT_META: Record<
  AssessmentReview['verdict'],
  { label: string; className: string }
> = {
  agree: { label: '复核同意', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  partial: { label: '部分分歧', className: 'border-amber-200 bg-amber-50 text-amber-700' },
  disagree: { label: '存在分歧', className: 'border-destructive/30 bg-destructive/10 text-destructive' },
};

export function AssessmentReviewCard({ data }: { data: SubmissionDetail }) {
  const { t } = useLanguage();
  const [copied, setCopied] = useState(false);
  const review = data.assessment_review;
  const isStale = review !== null && review.reviewed_revision !== data.grading_revision;
  const canRequestReview = data.status === 'ready_for_review';

  const copyReviewPrompt = async () => {
    const prompt =
      `请使用 AI-Marking MCP 独立复核作业 #${data.id} 的评分建议：` +
      `先调用 open_ai_marking_assignment(${data.id}) 用 continuation_token 读完评分包（header 中的 current_assessment 是被复核建议），` +
      `按 grading_policy 独立复核每个评分项，再调用 ` +
      `save_ai_marking_assessment_review(${data.id}, grading_handle, verdict, summary, items, confidence) ` +
      `保存逐项复核结论（items 逐项引用 rubric_item_id，verdict 为 agree/disagree，分歧项可附 suggested_score）。`;
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
      toast.success(t('AI 复核指令已复制，请粘贴给编程助手'));
    } catch {
      toast.error(t('复制失败，请手动选择指令'));
    }
  };

  if (!review) {
    if (!canRequestReview) return null;
    return (
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border bg-white px-5 py-2.5">
        <p className="text-xs text-muted-foreground">
          {t('尚无 AI 复核；可将作业交给另一个编程助手任务独立复核。')}
        </p>
        <Button size="sm" variant="outline" className="h-7 shrink-0 gap-1.5 text-xs" onClick={copyReviewPrompt}>
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? t('已复制') : t('发起 AI 复核')}
        </Button>
      </div>
    );
  }

  const meta = REVIEW_VERDICT_META[review.verdict] ?? REVIEW_VERDICT_META.partial;
  const clientLabel = review.client ? CLIENT_LABELS[review.client] ?? review.client : null;
  const disagreeItems = review.items.filter((item) => item.verdict === 'disagree');

  return (
    <div
      className={`shrink-0 border-b px-5 py-3 ${
        isStale ? 'border-amber-200 bg-amber-50/60' : 'border-border bg-white'
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className={`h-5 gap-1 px-2 text-[10px] font-medium ${meta.className}`}>
          {t('AI 复核')} · {t(meta.label)}
        </Badge>
        {clientLabel && (
          <span className="text-[10px] text-muted-foreground">{t('来自')} {clientLabel}</span>
        )}
        <span className="text-[10px] text-muted-foreground">
          {dayjs(review.created_at).format('MM-DD HH:mm')}
        </span>
        {isStale && (
          <span className="text-[10px] font-medium text-amber-700">
            {t('建议已更新（当前 revision')} {data.grading_revision}，{t('复核针对 revision')}{' '}
            {review.reviewed_revision}{t('），此结论已过期')}
          </span>
        )}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-foreground">{review.summary}</p>
      {review.items.length > 0 && (
        <details className="mt-2 rounded-lg border border-border bg-slate-50">
          <summary className="cursor-pointer select-none px-3 py-1.5 text-[11px] font-medium text-muted-foreground">
            {t('逐项复核结论')}{disagreeItems.length > 0 ? `（${disagreeItems.length} ${t('项分歧')}）` : ''}
          </summary>
          <ul className="space-y-2 border-t border-border px-3 py-2">
            {review.items.map((item) => (
              <li key={item.rubric_item_id} className="text-[11px] leading-relaxed">
                <span
                  className={`font-medium ${
                    item.verdict === 'disagree' ? 'text-destructive' : 'text-emerald-700'
                  }`}
                >
                  {item.verdict === 'disagree' ? t('分歧') : t('同意')} · {item.criterion}
                </span>
                {item.suggested_score !== null && (
                  <span className="ml-1.5 text-muted-foreground">
                    （{t('复核建议')} {item.suggested_score}/{item.max_score}）
                  </span>
                )}
                <p className="mt-0.5 text-slate-600">{item.comment}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
      {canRequestReview && (
        <div className="mt-2 flex justify-end">
          <Button size="sm" variant="ghost" className="h-6 gap-1.5 px-2 text-[11px] text-muted-foreground" onClick={copyReviewPrompt}>
            {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
            {copied ? t('已复制') : t('重新发起 AI 复核')}
          </Button>
        </div>
      )}
    </div>
  );
}
