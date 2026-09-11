import type { SubmissionDetail } from '@/api/submissions';
import { useLanguage } from '@/i18n';

export function CodeEvidencePanel({ data }: { data: SubmissionDetail }) {
  const { t } = useLanguage();
  if (!data.code_files?.length && !data.code_input_files?.length) return null;
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-5">
      <div className="mb-4 rounded-xl border border-primary/15 bg-white p-4">
        <p className="text-sm font-semibold">{t('提交代码')}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {t('新作业由编程助手在当前任务中运行和核验；后端只保存源码与 SHA-256。')}
        </p>
      </div>
      {data.code_files?.length ? (
        <div className="space-y-4">
          {data.code_files.map((codeFile) => {
            return (
              <div key={codeFile.id} className="rounded-xl border bg-white p-4 shadow-sm">
                {/* Q{N} 风格与上传页 ZIP 预览一致;不复用「第」(那是分页语义) */}
                <p className="text-sm font-semibold">{`Q${codeFile.question_number}`} · {codeFile.original_filename}</p>
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
      ) : null}
      {data.code_input_files?.length ? (
        <div className="mt-4 rounded-xl border bg-white p-4 shadow-sm">
          <p className="text-sm font-semibold">{t('数据集文件')}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t('批改时随代码一并物化到隔离工作区,供程序运行读取。')}
          </p>
          <ul className="mt-3 divide-y rounded-lg border bg-slate-50">
            {data.code_input_files.map((inputFile) => (
              <li key={inputFile.id} className="flex items-center justify-between px-3 py-2 text-xs">
                <span className="truncate font-medium" title={inputFile.original_filename}>
                  {inputFile.original_filename}
                </span>
                <span className="ml-3 shrink-0 text-muted-foreground">
                  {(inputFile.size_bytes / 1024 / 1024).toFixed(2)} MB
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
