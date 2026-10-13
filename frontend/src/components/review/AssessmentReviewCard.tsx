import dayjs from 'dayjs';
import type { AssessmentReview, SubmissionDetail } from '@/api/submissions';
import { Badge } from '@/components/ui/badge';
import { useLanguage } from '@/i18n';

const REVIEW_VERDICT_META: Record<
  AssessmentReview['verdict'],
  { label: string; className: string }
> = {
  agree: { label: '复核同意', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  partial: { label: '部分分歧', className: 'border-amber-200 bg-amber-50 text-amber-700' },
  disagree: { label: '存在分歧', className: 'border-destructive/30 bg-destructive/10 text-destructive' },
};

/**
 * 独立复核结论展示(只读)。
 *
 * v1 复核能力仅保留数据字段与展示;发起入口(旧的外部助手指令复制)已移除。
 */
export function AssessmentReviewCard({ data }: { data: SubmissionDetail }) {
  const { t } = useLanguage();
  const review = data.assessment_review;
  const isStale = review !== null && review.reviewed_revision !== data.grading_revision;

  if (!review) return null;

  const meta = REVIEW_VERDICT_META[review.verdict] ?? REVIEW_VERDICT_META.partial;
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
        {review.client && (
          <span className="text-[10px] text-muted-foreground">
            {t('来自')} {review.client}
          </span>
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
            {t('逐项复核结论')}
            {disagreeItems.length > 0 ? `（${disagreeItems.length} ${t('项分歧')}）` : ''}
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
    </div>
  );
}
