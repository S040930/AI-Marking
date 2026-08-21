import type { SubmissionDetail } from '@/api/submissions';
import { useLanguage } from '@/i18n';

export function CodeEvidencePanel({ data }: { data: SubmissionDetail }) {
  const { t } = useLanguage();
  if (!data.code_files?.length) return null;
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-5">
      <div className="mb-4 rounded-xl border border-primary/15 bg-white p-4">
        <p className="text-sm font-semibold">{t('提交代码')}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {t('新作业由编程助手在当前任务中运行和核验；后端只保存源码与 SHA-256。')}
        </p>
      </div>
      <div className="space-y-4">
        {data.code_files.map((codeFile) => {
          return (
            <div key={codeFile.id} className="rounded-xl border bg-white p-4 shadow-sm">
              <p className="text-sm font-semibold">{t('第')} {codeFile.question_number} {t('题')} · {codeFile.original_filename}</p>
              <p className="mt-1 text-xs text-muted-foreground">SHA-256：{codeFile.source_sha256}</p>
              <details className="mt-3 rounded-lg border bg-slate-50">
                <summary className="cursor-pointer px-3 py-2 text-xs font-medium">{t('查看提交源代码')}</summary>
                <pre className="max-h-80 overflow-auto border-t p-3 text-xs leading-relaxed">
                  {codeFile.source_text || t('源代码不可用')}
                </pre>
              </details>
            </div>
          );
        })}
      </div>
    </div>
  );
}
