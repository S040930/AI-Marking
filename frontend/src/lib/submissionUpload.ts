import type { SubmissionCodeInput } from '@/api/submissions';

export interface CodeEntry {
  id: string;
  file: File;
  questionNumber: string;
}

export type CodeInputError =
  | { type: 'invalid_question'; filename: string }
  | { type: 'missing_entrypoint' };

export type CodeInputValidation =
  | { ok: true; inputs: SubmissionCodeInput[] }
  | { ok: false; error: CodeInputError };

// Keep in sync with the backend's code_manifest.auto_question_number rule.
export function autoQuestionNumber(filename: string): number | null {
  const match = /q(\d+)\.[A-Za-z0-9_.-]+$/i.exec(filename);
  return match ? Number(match[1]) : null;
}

export function formatZipSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function buildSubmissionCodeInputs(
  entries: CodeEntry[],
  entrypointIds: Record<number, string>,
): CodeInputValidation {
  const invalidEntry = entries.find((entry) => {
    const questionNumber = Number(entry.questionNumber);
    return !Number.isInteger(questionNumber) || questionNumber < 1;
  });
  if (invalidEntry) {
    return {
      ok: false,
      error: { type: 'invalid_question', filename: invalidEntry.file.name },
    };
  }

  const inputs: SubmissionCodeInput[] = [];
  for (const entry of entries) {
    const questionNumber = Number(entry.questionNumber);
    const entrypointId = entrypointIds[questionNumber];
    if (entrypointId === undefined) {
      return { ok: false, error: { type: 'missing_entrypoint' } };
    }
    inputs.push({
      file: entry.file,
      questionNumber,
      entrypoint: entrypointId === entry.id,
    });
  }
  return { ok: true, inputs };
}
