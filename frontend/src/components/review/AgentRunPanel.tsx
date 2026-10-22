import { useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { Loader2, Play, Settings } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  isTerminalAgentRun,
  useActiveAgentRun,
  useAgentProviders,
  useAgentRunEvents,
  useCreateAgentRun,
  type AgentProviderName,
  type AgentRun,
} from '@/api/agent';
import { Button } from '@/components/ui/button';
import { AgentRunProgress } from '@/components/review/AgentRunProgress';
import { AgentModelSelector } from '@/components/review/AgentModelSelector';
import { runStatusLabel } from '@/lib/agentRunStatus';
import { effortOptionsFor } from '@/lib/effortOptions';
import { useLanguage } from '@/i18n';

/**
 * awaiting_grading 状态下的批改入口与运行视图。
 *
 * 无活跃 run:教师选择通道/模型/思考强度后点「开始批改」,由后端进程内
 * agent 完成评分(不再依赖外部编程助手)。有活跃 run:展示进度、运行中
 * 配置切换、取消与检查点答复。失败/取消后可重新配置发起。
 */
export function AgentRunPanel({
  submissionId,
  questionName,
  hasCode,
}: {
  submissionId: number;
  questionName?: string | null;
  hasCode?: boolean;
}) {
  const {
    data: runDetail,
    isLoading,
    isError,
    error,
  } = useActiveAgentRun(submissionId);
  // 取消/失败后 by-submission 返回 404,query 进入 error 状态但 data 停留
  // 旧值(如 cancelling);必须以 404 为准视为无活跃 run,否则页面卡死。
  const noActiveRun =
    isError && (error as { response?: { status?: number } })?.response?.status === 404;
  const run = noActiveRun ? undefined : runDetail?.run;
  const events = useAgentRunEvents(
    run?.id,
    run !== undefined && !isTerminalAgentRun(run.status),
    submissionId,
  );

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Loader2 className="size-8 animate-spin text-primary" />
      </div>
    );
  }

  if (run === undefined) {
    return (
      <PanelShell>
        <AgentStartPanel submissionId={submissionId} questionName={questionName} hasCode={hasCode} />
      </PanelShell>
    );
  }

  if (isTerminalAgentRun(run.status)) {
    return (
      <PanelShell>
        <AgentFinishedPanel
          run={run}
          submissionId={submissionId}
          questionName={questionName}
          hasCode={hasCode}
        />
      </PanelShell>
    );
  }

  return (
    <PanelShell>
      <AgentRunProgress
        run={run}
        events={events}
        pendingConfig={runDetail?.pending_config}
      />
    </PanelShell>
  );
}

function PanelShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center overflow-y-auto p-8">
      <div className="w-full max-w-2xl">{children}</div>
    </div>
  );
}

function AgentStartPanel({
  submissionId,
  questionName,
  hasCode,
}: {
  submissionId: number;
  questionName?: string | null;
  hasCode?: boolean;
}) {
  const { t } = useLanguage();
  const { data: providersData, isLoading } = useAgentProviders();
  const createMutation = useCreateAgentRun(submissionId);
  const [provider, setProvider] = useState<AgentProviderName | null>(null);
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [regenerateTests, setRegenerateTests] = useState(false);

  const defaultProvider = (providersData?.provider_default ?? 'openai_api') as AgentProviderName;
  const activeProvider = provider ?? defaultProvider;
  const status = providersData?.providers[activeProvider];
  const subscription = providersData?.providers['chatgpt_sub'];
  const subscriptionReady = Boolean(subscription?.available);
  const ready = Boolean(status?.available && status?.configured);
  const effortOptions = effortOptionsFor(activeProvider);

  const start = () => {
    createMutation.mutate(
      {
        provider: activeProvider,
        model: model.trim() || null,
        reasoning_effort: effort || null,
        regenerate_code_tests: hasCode ? regenerateTests : false,
      },
      {
        onSuccess: () => toast.success(t('批改已启动')),
        onError: (err) => toast.error(err.message),
      },
    );
  };

  return (
    <div className="rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-wider text-primary">
        {questionName}
      </p>
      <h2 className="mt-1.5 text-xl font-semibold">{t('开始 AI 批改')}</h2>

      {isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {t('加载模型通道中...')}
        </div>
      ) : (
        <>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {t('批改由本机后端直接调用模型完成，不再需要外部编程助手。')}
          </p>

          {/* 通道选择 */}
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            <ChannelOption
              label={t('官方 OpenAI API')}
              detail={
                providersData?.providers['openai_api']?.configured
                  ? `${t('已配置')} ···${providersData.providers['openai_api'].key_last4 ?? ''}`
                  : t('未配置')
              }
              selected={activeProvider === 'openai_api'}
              disabled={false}
              onSelect={() => {
                setProvider('openai_api');
                setModel('');
                setEffort('');
              }}
            />
            <ChannelOption
              label={t('ChatGPT 订阅')}
              detail={
                subscriptionReady
                  ? `${t('已登录')}${subscription?.plan ? ` · ${subscription.plan}` : ''}`
                  : t('未登录')
              }
              selected={activeProvider === 'chatgpt_sub'}
              disabled={!subscriptionReady}
              onSelect={() => {
                setProvider('chatgpt_sub');
                setModel('');
                setEffort('');
              }}
            />
          </div>

          {hasCode ? (
            <label className="mt-4 flex cursor-pointer items-start gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                className="mt-1 accent-primary"
                checked={regenerateTests}
                disabled={createMutation.isPending}
                onChange={(event) => setRegenerateTests(event.target.checked)}
              />
              <span>
                <span className="font-medium text-foreground">{t('重新生成此题的自动测试集')}</span>
                <span className="mt-0.5 block text-xs leading-relaxed">
                  {t('默认复用已批准版本；勾选后会生成新版本并在运行前请你审核。')}
                </span>
              </span>
            </label>
          ) : null}

          {/* 模型与思考强度 */}
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <AgentModelSelector
              provider={activeProvider}
              defaultModel={status?.default_model ?? ''}
              value={model}
              onChange={setModel}
            />
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              {t('思考强度')}
              <select
                className="h-9 rounded-lg border border-slate-200 bg-white px-2.5 text-sm outline-none focus:ring-2 focus:ring-primary/30"
                value={effort}
                aria-label={t('思考强度')}
                onChange={(e) => setEffort(e.target.value)}
              >
                {effortOptions.map((option) => (
                  <option key={option} value={option}>
                    {option || status?.reasoning_effort || t('默认思考强度')}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {ready ? (
            <Button className="mt-5" disabled={createMutation.isPending} onClick={start}>
              <Play className="size-4" />
              {t('开始批改')}
            </Button>
          ) : (
            <div className="mt-5 flex flex-wrap items-center gap-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-700">
              {activeProvider === 'chatgpt_sub' && !subscriptionReady
                ? t('请先在设置页登录 ChatGPT 订阅。')
                : t('请先在设置页配置官方 OpenAI API key。')}
              <Button asChild variant="outline" size="sm">
                <Link to="/settings">
                  <Settings className="size-4" />
                  {t('前往设置')}
                </Link>
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ChannelOption({
  label,
  detail,
  selected,
  disabled,
  onSelect,
}: {
  label: string;
  detail: string;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabled}
      onClick={onSelect}
      className={`rounded-xl border p-3 text-left transition-colors ${
        selected
          ? 'border-primary bg-primary/5'
          : 'border-slate-200 bg-white hover:border-primary/40'
      } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
    >
      <p className="text-sm font-medium text-foreground">{label}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
    </button>
  );
}

function AgentFinishedPanel({
  run,
  submissionId,
  questionName,
  hasCode,
}: {
  run: AgentRun;
  submissionId: number;
  questionName?: string | null;
  hasCode?: boolean;
}) {
  const { t } = useLanguage();
  const description =
    run.status === 'completed'
      ? t('评分建议已保存,页面将自动进入复核。')
      : run.status === 'failed'
        ? run.error_message ?? t('批改运行失败,可重新发起。')
        : t('批改运行已取消,可重新发起。');
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-primary/15 bg-white p-6 text-center shadow-sm">
        <h2 className="text-lg font-semibold">{t(runStatusLabel(run.status))}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{description}</p>
      </div>
      {run.status !== 'completed' ? (
        <AgentStartPanel submissionId={submissionId} questionName={questionName} hasCode={hasCode} />
      ) : null}
    </div>
  );
}
