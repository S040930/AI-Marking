import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Loader2, Sparkles } from 'lucide-react';
import {
  type AiSuggestion,
  type FinalizePayload,
} from '@/api/submissions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Empty } from '@/components/Empty';
import { useLanguage } from '@/i18n';

interface ManualReviewPanelProps {
  suggestion: AiSuggestion | null;
  isReadOnly: boolean;
  reviewerName: string;
  onFinalize: (payload: FinalizePayload) => void;
  isFinalizing: boolean;
  className?: string;
}

interface EditableDetail {
  criterion: string;
  // '' 表示输入框被清空的中间态:不清零、不参与合计,提交前必须补全
  score: number | '';
  max_score: number;
  comment: string;
  evidence: string[];
}

function scoreIsValid(item: EditableDetail): item is EditableDetail & { score: number } {
  return (
    typeof item.score === 'number' &&
    Number.isFinite(item.score) &&
    item.score >= 0 &&
    item.score <= item.max_score
  );
}

function toEditable(suggestion: AiSuggestion | null): EditableDetail[] {
  if (!suggestion) return [];
  return (suggestion.details ?? []).map((d) => ({
    criterion: d.criterion,
    score: d.score,
    max_score: d.max_score ?? d.score,
    comment: d.comment ?? '',
    evidence: d.evidence ?? [],
  }));
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

export default function ManualReviewPanel({
  suggestion,
  isReadOnly,
  reviewerName,
  onFinalize,
  isFinalizing,
  className,
}: ManualReviewPanelProps) {
  const { t } = useLanguage();
  const [details, setDetails] = useState<EditableDetail[]>(() =>
    toEditable(suggestion),
  );
  const [feedback, setFeedback] = useState(suggestion?.feedback ?? '');

  // 建议内容变化时重置工作副本(仅在内容实际变化时),避免用户编辑被覆盖。
  const suggestionKey = JSON.stringify(suggestion);
  useEffect(() => {
    setDetails(toEditable(suggestion));
    setFeedback(suggestion?.feedback ?? '');
  }, [suggestionKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const maxScore = suggestion?.max_score ?? 0;
  const totalScore = useMemo(
    () =>
      details.reduce(
        (sum, item) =>
          typeof item.score === 'number' && Number.isFinite(item.score)
            ? sum + item.score
            : sum,
        0,
      ),
    [details],
  );
  const totalMax = useMemo(
    () => details.reduce((sum, item) => sum + (item.max_score || 0), 0),
    [details],
  );
  // The item scores form the proposed final score; they do not need to equal
  // the rubric's maximum unless the student received full marks.
  const totalsMatch = totalScore <= maxScore + 0.01;
  const totalMaxMatch = Math.abs(totalMax - maxScore) < 0.01;
  // 每一项都已填写且在 [0, 满分] 内才允许提交;空串/负数/超满分均视为非法
  const allScoresValid = details.every(scoreIsValid);
  const invalidCriteria = details
    .filter((item) => !scoreIsValid(item))
    .map((item) => item.criterion);
  const canSubmit =
    !isReadOnly &&
    suggestion !== null &&
    details.length > 0 &&
    allScoresValid &&
    totalsMatch &&
    totalMaxMatch &&
    feedback.trim().length > 0;

  const updateDetail = (index: number, patch: Partial<EditableDetail>) => {
    setDetails((prev) =>
      prev.map((item, idx) => (idx === index ? { ...item, ...patch } : item)),
    );
  };

  const handleSubmit = () => {
    if (!canSubmit) return;
    onFinalize({
      reviewer_name: reviewerName,
      score: totalScore,
      max_score: maxScore,
      feedback: feedback.trim(),
      details: details.map((d) => ({
        criterion: d.criterion,
        // canSubmit 已保证全部为合法数字
        score: d.score as number,
        max_score: d.max_score,
        comment: d.comment,
        evidence: d.evidence ?? [],
      })),
    });
  };

  if (!suggestion) {
    return (
      <div className={`flex h-full items-center justify-center p-8 ${className ?? ''}`}>
        <Empty text={t('还没有评分建议')} />
      </div>
    );
  }

  return (
    <div className={`flex h-full flex-col overflow-hidden ${className ?? ''}`}>
      <div className="flex-1 overflow-y-auto bg-slate-50/90 p-5">
        <div className="animate-score-card-enter rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                {t('编程助手建议评分')}
              </p>
              <p className="mt-0.5 text-2xl font-bold tracking-tight text-foreground">
                {totalScore}
                <span className="ml-0.5 text-base font-medium text-muted-foreground">
                  /{maxScore}
                </span>
              </p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge
                variant="secondary"
                className="h-5 shrink-0 gap-1 px-2 text-[10px] font-medium"
              >
                <Sparkles className="size-3" />
                {t('置信度')} {Math.round((suggestion.confidence ?? 0) * 100)}%
              </Badge>
              <p className="text-[10px] text-muted-foreground">
                {t('最终成绩以教师确认提交为准')}
              </p>
            </div>
          </div>

          {/* 反馈编辑 */}
          <label className="mb-1 block text-xs font-medium text-foreground">
            {t('总评反馈')}
          </label>
          <Textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            disabled={isReadOnly}
            placeholder={t('输入最终反馈…')}
            className="min-h-[72px] resize-none rounded-lg border-border bg-background px-3 py-2.5 text-sm leading-relaxed shadow-sm"
          />

          {/* 逐项改分 */}
          <div className="mt-5 space-y-4">
            {details.map((item, idx) => {
              const scoreNumeric = typeof item.score === 'number';
              const negative = scoreNumeric && (item.score as number) < 0;
              const outOfRange =
                scoreNumeric &&
                (!Number.isFinite(item.score) || negative || (item.score as number) > item.max_score);
              return (
                <div
                  key={idx}
                  className="space-y-2 border-b border-slate-100 pb-4 last:border-b-0 last:pb-0"
                >
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <span className="font-medium text-foreground">
                      {item.criterion}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <Input
                        type="number"
                        min={0}
                        max={item.max_score}
                        step={0.5}
                        value={item.score}
                        disabled={isReadOnly}
                        onChange={(e) =>
                          updateDetail(idx, {
                            // 空串保留为 ''(不清零),其余转数字
                            score: e.target.value === '' ? '' : Number(e.target.value),
                          })
                        }
                        className={`h-7 w-16 px-2 text-right tabular-nums ${
                          outOfRange ? 'border-destructive text-destructive' : ''
                        }`}
                      />
                      <span className="tabular-nums text-muted-foreground">
                        /{item.max_score}
                      </span>
                    </div>
                  </div>
                  <ScoreBar
                    score={scoreNumeric && Number.isFinite(item.score) ? (item.score as number) : 0}
                    maxScore={item.max_score}
                  />
                  {outOfRange && (
                    <p className="text-[10px] text-destructive">
                      {negative
                        ? t('单项得分不能为负数')
                        : t('单项得分不能超过该项满分')}
                    </p>
                  )}
                  <Textarea
                    value={item.comment}
                    onChange={(e) =>
                      updateDetail(idx, { comment: e.target.value })
                    }
                    disabled={isReadOnly}
                    placeholder={t('该项评分说明…')}
                    className="min-h-[48px] resize-none rounded-lg border-border bg-slate-50 px-3 py-2 text-[11px] leading-relaxed shadow-sm"
                  />
                  {(item.evidence?.length ?? 0) > 0 && (
                    <div className="rounded-md border border-blue-100 bg-blue-50/70 px-2.5 py-2">
                      <p className="mb-1 text-[10px] font-medium text-blue-700">
                        {t('原文证据')}
                      </p>
                      <ul className="space-y-1 text-[10px] leading-relaxed text-slate-600">
                        {item.evidence.map((evidence, evidenceIndex) => (
                          <li key={evidenceIndex}>“{evidence}”</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* 分数一致性提示 */}
          {!allScoresValid && (
            <p className="mt-4 text-xs text-destructive">
              {t('以下评分项得分未填写或非法：')}
              {invalidCriteria.join('、')}
            </p>
          )}
          {!totalsMatch && (
            <p className="mt-4 text-xs text-destructive">
              {t('各评分项得分之和')} {totalScore} {t('超过总分')} {maxScore}，{t('暂不可提交。')}
            </p>
          )}
          {!totalMaxMatch && totalsMatch && (
            <p className="mt-4 text-xs text-destructive">
              {t('各评分项满分之和')} {totalMax} {t('与总满分')} {maxScore} {t('不一致，暂不可提交。')}
            </p>
          )}
        </div>
      </div>

      {/* 提交区 */}
      <div className="shrink-0 border-t border-slate-200 bg-white/95 p-4">
        {isReadOnly ? (
          <div className="flex items-center justify-center gap-2 rounded-xl border border-border bg-muted/40 py-3 text-sm text-muted-foreground">
            <CheckCircle2 className="size-4" />
            {t('该作业已审阅，成绩已锁定')}
          </div>
        ) : (
          <Button
            onClick={handleSubmit}
            disabled={!canSubmit || isFinalizing}
            className="w-full gap-2"
          >
            {isFinalizing ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <CheckCircle2 className="size-4" />
            )}
            {t('复核并提交最终评分')}
          </Button>
        )}
      </div>
    </div>
  );
}
