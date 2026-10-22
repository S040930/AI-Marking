import { toast } from 'sonner';
import { useReplyAgentCheckpoint } from '@/api/agent';
import type { CodeTestCase, CodeTestResult } from '@/api/agent';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';
import { degradedIsolation, isolationLabel, testStatusLabel } from '@/lib/codeTestStatus';

export function CodeTestSuiteCheckpoint({
  runId,
  message,
  preview,
}: {
  runId: number;
  message: string;
  preview: { suite_id?: number; version?: number; tests?: CodeTestCase[] };
}) {
  const { t } = useLanguage();
  const replyMutation = useReplyAgentCheckpoint(runId);
  const reply = (action: 'approve_test_suite' | 'regenerate_test_suite') =>
    replyMutation.mutate(
      { action },
      {
        onSuccess: () =>
          toast.success(
            action === 'approve_test_suite' ? t('测试集已批准') : t('正在重新生成测试集'),
          ),
        onError: (error) => toast.error(error.message),
      },
    );
  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-medium text-amber-900">{t('审核自动测试集')} · v{preview.version}</h3>
          <p className="mt-1 text-sm leading-relaxed text-amber-800">{message}</p>
        </div>
        <span className="rounded-full bg-white/80 px-2 py-1 text-xs text-amber-800">
          {preview.tests?.length ?? 0} {t('个测试项')}
        </span>
      </div>
      <div className="mt-3 max-h-72 space-y-2 overflow-y-auto">
        {(preview.tests ?? []).map((test) => (
          <TestCasePreview key={test.id} test={test} />
        ))}
      </div>
      <p className="mt-3 text-xs leading-relaxed text-amber-700">
        {t('测试集仅根据题目 OCR 与可信 rubric 生成。批准后同题作业复用此版本；测试结果会提供给 AI 作为评分证据。')}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={replyMutation.isPending}
          onClick={() => reply('approve_test_suite')}
        >
          {t('批准并运行测试')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={replyMutation.isPending}
          onClick={() => reply('regenerate_test_suite')}
        >
          {t('重新生成')}
        </Button>
      </div>
    </div>
  );
}

function TestCasePreview({ test }: { test: CodeTestCase }) {
  const { t } = useLanguage();
  return (
    <article className="rounded-xl border border-amber-100 bg-white p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="font-semibold text-foreground">Q{test.question_number} · {test.title}</span>
        <span>{test.language}</span>
        <span>{t('标准输入/输出')}</span>
      </div>
      {test.mode === 'program_io' ? (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <pre className="overflow-x-auto rounded-lg bg-slate-50 p-2 text-xs">{`${t('输入')}\n${test.input ?? ''}`}</pre>
          <pre className="overflow-x-auto rounded-lg bg-slate-50 p-2 text-xs">{`${t('预期输出')}\n${test.expected_stdout ?? ''}`}</pre>
        </div>
      ) : null}
    </article>
  );
}

export function CodeTestResultsPanel({
  data,
}: {
  data: { version: number; summary: { total: number; passed: number; failed: number; not_run?: number }; results: CodeTestResult[] };
}) {
  const { t } = useLanguage();
  const degraded = data.results.find((result) => degradedIsolation(result.isolation))?.isolation;
  const isolationNote = isolationLabel(degraded);
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{t('自动测试结果')} · v{data.version}</h3>
        <span className="flex flex-wrap items-center gap-2">
          {isolationNote ? (
            <span
              className="rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-700"
              title={degraded}
            >
              {t(isolationNote)}
            </span>
          ) : null}
          <span className="text-xs text-muted-foreground">
            {data.summary.passed}/{data.summary.total - (data.summary.not_run ?? 0)} {t('项通过')}
          </span>
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {data.results.map((result) => (
          <article key={result.id} className="rounded-xl border border-slate-100 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-medium">Q{result.question_number} · {result.title}</p>
              <span className={`rounded-full px-2 py-0.5 text-xs ${result.status === 'passed' ? 'bg-emerald-50 text-emerald-700' : result.status === 'not_run' ? 'bg-slate-100 text-slate-600' : 'bg-rose-50 text-rose-700'}`}>
                {t(testStatusLabel(result.status))}
              </span>
            </div>
            {result.output ? <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-2 text-xs text-slate-600">{result.output}</pre> : null}
          </article>
        ))}
      </div>
    </section>
  );
}
