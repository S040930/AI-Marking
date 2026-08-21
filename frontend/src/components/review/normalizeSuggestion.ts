import type { AiSuggestion, SubmissionDetail } from '@/api/submissions';

export function normalizeSuggestion(data: SubmissionDetail): AiSuggestion | null {
  const raw = data.assessment_suggestion;
  if (!raw) return null;
  return {
    score: raw.score ?? 0,
    max_score: raw.max_score ?? 0,
    confidence: raw.confidence ?? 0,
    feedback: raw.feedback ?? '',
    details: (raw.details ?? []).map((d) => ({
      criterion: d.criterion,
      score: d.score,
      max_score: d.max_score ?? 0,
      comment: d.comment,
      evidence: d.evidence,
    })),
  };
}
