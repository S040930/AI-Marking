import { useState } from 'react';
import { toast } from 'sonner';
import { Loader2, Square } from 'lucide-react';
import {
  isTerminalAgentRun,
  useCancelAgentRun,
  useReplyAgentCheckpoint,
  useUpdateAgentRunConfiguration,
  type AgentRun,
  type AgentRunEvent,
} from '@/api/agent';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';
import { cn } from '@/lib/utils';
import { runStatusLabel } from '@/lib/agentRunStatus';

/** 常用思考强度档位(Responses API reasoning.effort);空串表示用默认。 */
const EFFORT_OPTIONS = ['', 'minimal', 'low', 'medium', 'high'] as const;

/**
 * 批改运行进行中的公共视图:状态头 + 运行配置切换 + 取消/检查点操作 + 转录。
 */
export function AgentRunProgress({
  run,
  events,
  pendingConfig,
  showTranscript = true,
  className,
}: {
  run: AgentRun;
  events: AgentRunEvent[];
  /** 已排队等待下一回合生效的配置(来自 run 详情响应)。 */
  pendingConfig?: Record<string, unknown> | null;
  showTranscript?: boolean;
  className?: string;
}) {
  return (
    <div className={cn('space-y-4', className)}>
      <AgentRunHeader run={run} />
      {isTerminalAgentRun(run.status) || run.status === 'cancelling' ? null : (
        <RunConfigSwitcher run={run} pending={Boolean(pendingConfig)} />
      )}
      {run.status === 'waiting_for_teacher' && run.checkpoint ? (
        <AgentCheckpointForm runId={run.id} message={run.checkpoint.message} />
      ) : (
        <AgentCancelButton runId={run.id} />
      )}
      {showTranscript ? <AgentTranscript events={events} /> : null}
    </div>
  );
}

export function AgentRunHeader({ run }: { run: AgentRun }) {
  const { t } = useLanguage();
  const usage = run.usage as
    | { input_tokens?: number; output_tokens?: number; total_tokens?: number }
    | null;
  return (
    <div className="rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
      <p className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-primary">
        {t('AI 批改运行')} #{run.id}
      </p>
      <div className="mt-2 flex items-center gap-2">
        {isTerminalAgentRun(run.status) ? null : (
          <Loader2 className="size-4 animate-spin text-primary" />
        )}
        <h2 className="text-lg font-semibold">{t(runStatusLabel(run.status))}</h2>
        <span className="text-xs text-muted-foreground">
          {t('尝试次数')}: {run.attempts}
        </span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {run.provider} · {run.model ?? t('默认模型')} ·{' '}
        {run.reasoning_effort ?? t('默认思考强度')}
      </p>
      {usage && (usage.input_tokens || usage.output_tokens) ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {t('Token 用量')}: {usage.input_tokens ?? 0} in / {usage.output_tokens ?? 0} out
        </p>
      ) : null}
      {run.error_message ? (
        <p className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">
          {run.error_message}
        </p>
      ) : null}
    </div>
  );
}

/**
 * 运行中热切换模型/思考强度(PATCH /agent/runs/{id}/configuration)。
 * 生效时机由后端按 run 状态决定:未启动直接生效,进行中排队到下一回合。
 * provider 绑定会话,运行中不可切换。
 */
function RunConfigSwitcher({
  run,
  pending,
}: {
  run: AgentRun;
  pending: boolean;
}) {
  const { t } = useLanguage();
  const [model, setModel] = useState(run.model ?? '');
  const [effort, setEffort] = useState(run.reasoning_effort ?? '');
  const updateMutation = useUpdateAgentRunConfiguration(run.id);

  const apply = () => {
    const body: { model?: string | null; reasoning_effort?: string | null } = {};
    if (model.trim() !== (run.model ?? '')) body.model = model.trim() || null;
    if (effort !== (run.reasoning_effort ?? '')) {
      body.reasoning_effort = effort || null;
    }
    if (Object.keys(body).length === 0) {
      toast.info(t('配置没有变化'));
      return;
    }
    updateMutation.mutate(body, {
      onSuccess: (data) =>
        toast.success(
          data.run.effective_at === 'next_turn'
            ? t('配置已排队,将在下一回合生效')
            : t('配置已更新'),
        ),
      onError: (err) => toast.error(err.message),
    });
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-3">
      <p className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t('运行配置')}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          className="h-8 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:ring-2 focus:ring-primary/30"
          placeholder={t('模型 ID(留空用默认)')}
          value={model}
          aria-label={t('模型 ID')}
          onChange={(e) => setModel(e.target.value)}
        />
        <select
          className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:ring-2 focus:ring-primary/30"
          value={effort}
          aria-label={t('思考强度')}
          onChange={(e) => setEffort(e.target.value)}
        >
          {EFFORT_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option || t('默认思考强度')}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          variant="outline"
          className="h-8 text-xs"
          disabled={updateMutation.isPending}
          onClick={apply}
        >
          {t('应用')}
        </Button>
      </div>
      {pending ? (
        <p className="mt-2 text-[11px] text-amber-600">
          {t('新配置将在下一回合生效')}
        </p>
      ) : null}
    </div>
  );
}

export function AgentCancelButton({ runId }: { runId: number }) {
  const { t } = useLanguage();
  const cancelMutation = useCancelAgentRun(runId);
  return (
    <Button
      variant="outline"
      disabled={cancelMutation.isPending}
      onClick={() =>
        cancelMutation.mutate(undefined, {
          onSuccess: () => toast.success(t('已请求取消')),
          onError: (err) => toast.error(err.message),
        })
      }
    >
      <Square className="size-4" />
      {t('取消批改')}
    </Button>
  );
}

export function AgentCheckpointForm({
  runId,
  message,
}: {
  runId: number;
  message: string;
}) {
  const { t } = useLanguage();
  const [note, setNote] = useState('');
  const replyMutation = useReplyAgentCheckpoint(runId);
  const reply = (verdict: 'consistent' | 'mismatch') =>
    replyMutation.mutate(
      { verdict, note: note.trim() || undefined },
      {
        onSuccess: () => toast.success(t('答复已保存,批改将继续')),
        onError: (err) => toast.error(err.message),
      },
    );
  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
      <h3 className="font-medium text-amber-800">{t('教师检查点')}</h3>
      <p className="mt-1 text-sm leading-relaxed text-amber-800">{message}</p>
      <p className="mt-2 text-xs leading-relaxed text-amber-700">
        {t(
          '可在本地运行学生代码后核对结果再确认，或直接确认；选择"不一致"时必须填写说明。',
        )}
      </p>
      <textarea
        className="mt-3 w-full rounded-xl border border-amber-200 bg-white p-2 text-sm outline-none focus:ring-2 focus:ring-amber-300"
        rows={2}
        placeholder={t('备注(可选)')}
        aria-label={t('检查点备注')}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="mt-3 flex gap-2">
        <Button size="sm" disabled={replyMutation.isPending} onClick={() => reply('consistent')}>
          {t('确认一致,继续')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={replyMutation.isPending}
          onClick={() => reply('mismatch')}
        >
          {t('不一致,要求修正')}
        </Button>
      </div>
    </div>
  );
}

export function AgentTranscript({ events }: { events: AgentRunEvent[] }) {
  const { t } = useLanguage();
  if (events.length === 0) return null;
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {t('执行转录')}
      </h3>
      <ul className="mt-2 max-h-72 space-y-1.5 overflow-y-auto text-sm">
        {events.map((event) => {
          const text =
            (event.payload.text as string | undefined) ??
            (event.payload.title as string | undefined) ??
            '';
          if (!text) return null;
          return (
            <li key={event.seq} className="leading-relaxed text-slate-700">
              <span className="mr-1.5 font-mono text-[10px] uppercase text-slate-400">
                {event.kind}
              </span>
              {text}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
