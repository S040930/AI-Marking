import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Loader2, Square } from 'lucide-react';
import {
  isTerminalRun,
  selectionToRequest,
  useCancelAcpRun,
  useCodexConfiguration,
  useReplyAcpCheckpoint,
  useUpdateRunCodexConfiguration,
  type AcpConfigSnapshot,
  type AcpRun,
  type AcpRunEvent,
  type CodexSelection,
} from '@/api/acp';
import { Button } from '@/components/ui/button';
import { FastModeToggle, ModelEffortDropdown } from './acp-chat/ChatDraftToolbar';
import { useLanguage } from '@/i18n';
import { cn } from '@/lib/utils';
import { runStatusLabel } from '@/lib/acpRunStatus';

/**
 * 批改运行进行中的公共视图:状态头 + 取消/检查点操作 + 可选转录。
 *
 * 批改页中间栏与右侧 AI 助手共用同一份:中间栏展示完整转录负责「观察」,
 * 助手面板只放状态与操作负责「控制」(``showTranscript=false``),避免同一
 * 份转录在窄屏里重复渲染。
 */
export function AcpRunProgress({
  run,
  events,
  showTranscript = true,
  className,
}: {
  run: AcpRun;
  events: AcpRunEvent[];
  showTranscript?: boolean;
  className?: string;
}) {
  return (
    <div className={cn('space-y-4', className)}>
      <AcpRunHeader run={run} />
      {isTerminalRun(run.status) || run.status === 'cancelling' ? null : (
        <RunConfigSwitcher run={run} />
      )}
      {run.status === 'waiting_for_teacher' && run.checkpoint ? (
        <AcpCheckpointForm runId={run.id} message={run.checkpoint.message} />
      ) : (
        <AcpCancelButton runId={run.id} />
      )}
      {showTranscript ? <AcpTranscript events={events} /> : null}
    </div>
  );
}

export function AcpRunHeader({ run }: { run: AcpRun }) {
  const { t } = useLanguage();
  return (
    <div className="rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-primary">{t('Codex ACP 批改运行')} #{run.id}</p>
      </div>
      <div className="mt-2 flex items-center gap-2"><Loader2 className="size-4 animate-spin text-primary" /><h2 className="text-lg font-semibold">{t(runStatusLabel(run.status))}</h2><span className="text-xs text-muted-foreground">{t('尝试次数')}: {run.attempts}</span></div>
      <p className="mt-2 text-xs text-muted-foreground">{run.codex_config.model_id ?? t('Agent 默认模型')} · {run.codex_config.reasoning_effort ?? t('Agent 默认思考')} · {run.codex_config.speed_mode === 'fast' ? t('快速') : t('标准')} · {run.permission_mode === 'auto_review' ? t('Approve for me') : t('Ask for approval')}</p>
      {run.error_message ? <p className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{run.error_message}</p> : null}
    </div>
  );
}

function toSelection(snapshot: AcpConfigSnapshot): CodexSelection {
  return {
    modelId: snapshot.model_id ?? null,
    reasoningEffort: snapshot.reasoning_effort ?? null,
    speedMode: snapshot.speed_mode === 'fast' ? 'fast' : 'standard',
  };
}

/**
 * 运行中热切换模型/思考强度/速度(PATCH /acp/runs/{id}/codex-configuration)。
 * 生效时机由后端按 run 状态决定:未启动直接生效,进行中排队到下一回合。
 * 权限档位不在该接口范围内,运行中不可改。
 */
function RunConfigSwitcher({ run }: { run: AcpRun }) {
  const { t } = useLanguage();
  const [confirmFastOpen, setConfirmFastOpen] = useState(false);
  const [selection, setSelection] = useState<CodexSelection>(() =>
    toSelection(run.codex_config),
  );
  const { data: catalog, isFetching } = useCodexConfiguration(selection.modelId ?? null);
  const updateMutation = useUpdateRunCodexConfiguration(run.id);

  // 服务端确认后回灌:切模型可能连带调整思考/速度,避免本地停在旧值。
  const desiredKey = `${run.codex_config.model_id ?? ''}|${run.codex_config.reasoning_effort ?? ''}|${run.codex_config.speed_mode}`;
  useEffect(() => {
    setSelection(toSelection(run.codex_config));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.id, desiredKey]);

  if (!catalog) return null;

  const fast = catalog.speed_modes.find((option) => option.id === 'fast');
  const isFast = selection.speedMode === 'fast';
  const pending = run.pending_codex_config !== null;

  const apply = (next: CodexSelection) => {
    const previous = selection;
    setSelection(next);
    updateMutation.mutate(selectionToRequest(next), {
      onSuccess: (data) =>
        toast.success(
          data.run.effective_at === 'next_turn'
            ? t('配置已排队,将在下一回合生效')
            : t('配置已更新'),
        ),
      onError: (err) => {
        setSelection(previous);
        toast.error(err.message);
      },
    });
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-3">
      <div className="flex min-w-0 items-center gap-2">
        <p className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('运行配置')}
        </p>
        {isFetching ? (
          <Loader2
            className="size-3 shrink-0 animate-spin text-muted-foreground"
            aria-label={t('读取 Codex 配置能力中…')}
          />
        ) : null}
        <div className="ml-auto flex min-w-0 items-center gap-0.5">
          <FastModeToggle
            checked={isFast}
            disabled={updateMutation.isPending || !fast?.available}
            disabledReason={
              fast?.available
                ? undefined
                : t('当前账户、模型或 Agent 版本未声明快速模式，暂仅支持标准模式。')
            }
            confirming={confirmFastOpen}
            onConfirmingChange={setConfirmFastOpen}
            onToggle={() => {
              if (isFast) {
                apply({ ...selection, speedMode: 'standard' });
              } else {
                setConfirmFastOpen(true);
              }
            }}
            onConfirm={() => {
              setConfirmFastOpen(false);
              apply({ ...selection, speedMode: 'fast' });
            }}
            onCancel={() => {
              setConfirmFastOpen(false);
              setSelection({ ...selection, speedMode: 'standard' });
            }}
          />
          <ModelEffortDropdown
            catalog={catalog}
            selection={selection}
            disabled={updateMutation.isPending}
            onSelect={apply}
          />
        </div>
      </div>
      {pending ? (
        <p className="mt-2 text-[11px] text-amber-600">
          {t('新配置将在下一回合生效')}
        </p>
      ) : null}
    </div>
  );
}

export function AcpCancelButton({ runId }: { runId: number }) {
  const { t } = useLanguage();
  const cancelMutation = useCancelAcpRun(runId);
  return <Button variant="outline" disabled={cancelMutation.isPending} onClick={() => cancelMutation.mutate(undefined, { onSuccess: () => toast.success(t('已请求取消')), onError: (err) => toast.error(err.message) })}><Square className="size-4" />{t('取消批改')}</Button>;
}

export function AcpCheckpointForm({ runId, message }: { runId: number; message: string }) {
  const { t } = useLanguage();
  const [note, setNote] = useState('');
  const replyMutation = useReplyAcpCheckpoint(runId);
  const reply = (verdict: 'consistent' | 'mismatch') => replyMutation.mutate({ verdict, note: note.trim() || undefined }, { onSuccess: () => toast.success(t('答复已保存,批改将继续')), onError: (err) => toast.error(err.message) });
  return <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4"><h3 className="font-medium text-amber-800">{t('教师检查点')}</h3><p className="mt-1 text-sm leading-relaxed text-amber-800">{message}</p><textarea className="mt-3 w-full rounded-xl border border-amber-200 bg-white p-2 text-sm outline-none focus:ring-2 focus:ring-amber-300" rows={2} placeholder={t('备注(可选)')} value={note} onChange={(e) => setNote(e.target.value)} /><div className="mt-3 flex gap-2"><Button size="sm" disabled={replyMutation.isPending} onClick={() => reply('consistent')}>{t('确认一致,继续')}</Button><Button size="sm" variant="outline" disabled={replyMutation.isPending} onClick={() => reply('mismatch')}>{t('不一致,要求修正')}</Button></div></div>;
}

export function AcpTranscript({ events }: { events: AcpRunEvent[] }) {
  const { t } = useLanguage();
  if (events.length === 0) return null;
  return <div className="rounded-2xl border border-slate-200 bg-white p-4"><h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t('执行转录')}</h3><ul className="mt-2 max-h-72 space-y-1.5 overflow-y-auto text-sm">{events.map((event) => { const text = (event.payload.text as string | undefined) ?? (event.payload.title as string | undefined) ?? ''; if (!text) return null; return <li key={event.seq} className="leading-relaxed text-slate-700"><span className="mr-1.5 font-mono text-[10px] uppercase text-slate-400">{event.kind}</span>{text}</li>; })}</ul></div>;
}
