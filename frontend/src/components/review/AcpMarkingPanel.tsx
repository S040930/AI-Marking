import { Loader2, Play } from 'lucide-react';
import {
  type AcpRun,
  isTerminalRun,
  useActiveAcpRun,
  useAcpAgents,
  useAcpRunEvents,
} from '@/api/acp';
import { Button } from '@/components/ui/button';
import { McpWaitingPanel } from '@/components/review/McpWaitingPanel';
import { AcpRunProgress } from '@/components/review/AcpRunProgress';
import { runStatusLabel } from '@/lib/acpRunStatus';
import { useLanguage } from '@/i18n';

/**
 * awaiting_mcp 状态下的 Codex 批改入口。
 *
 * 批改不再从这里直接发起 run:点击「开始批改」由父级打开 AI 助手并填入
 * 批改指令,教师在助手里确认模型、思考强度与权限档位后手动发送;指令下发后
 * 本面板整栏让位给助手(见 ReviewPage 的批改模式两栏布局)。作业上若仍有
 * 历史 run(旧版本创建的),继续在这里展示进度、检查点与转录。
 */
export function AcpMarkingPanel({
  submissionId,
  questionName,
  questionId,
  onStartInAssistant,
}: {
  submissionId: number;
  questionName?: string | null;
  questionId?: string | null;
  /** 交给父级打开 AI 助手并预填批改指令。 */
  onStartInAssistant?: () => void;
}) {
  const {
    data: runDetail,
    isLoading,
    isError,
    error,
  } = useActiveAcpRun(submissionId);
  // 取消/失败后 by-submission 返回 404,query 进入 error 状态但 data 停留
  // 旧值(如 cancelling);必须以 404 为准视为无活跃 run,否则页面卡死。
  const noActiveRun =
    isError && (error as { response?: { status?: number } })?.response?.status === 404;
  const run = noActiveRun ? undefined : runDetail?.run;
  const events = useAcpRunEvents(
    run?.id,
    run !== undefined && !isTerminalRun(run.status),
    submissionId,
  );

  if (isLoading) {
    return <div className="flex flex-1 items-center justify-center"><Loader2 className="size-8 animate-spin text-primary" /></div>;
  }
  if (run === undefined) {
    return (
      <AcpStartPanel
        questionName={questionName}
        questionId={questionId}
        onStartInAssistant={onStartInAssistant}
      />
    );
  }
  if (isTerminalRun(run.status)) return <AcpFinishedPanel run={run} />;

  return (
    <div className="flex h-full flex-col items-center overflow-y-auto p-8">
      <div className="w-full max-w-2xl">
        <AcpRunProgress run={run} events={events} />
      </div>
    </div>
  );
}

function AcpStartPanel({
  questionName,
  questionId,
  onStartInAssistant,
}: {
  questionName?: string | null;
  questionId?: string | null;
  onStartInAssistant?: () => void;
}) {
  const { t } = useLanguage();
  const { data: agentsData, isLoading } = useAcpAgents();
  // 没有本地 Codex 连接时给出明确指引,而不是伪造可启动的表单。
  const codexReady = (agentsData?.agents ?? []).some(
    (agent) => agent.agent_id === 'codex-acp' && agent.connection_status === 'ready',
  );

  return (
    <div className="flex h-full flex-col items-center overflow-y-auto p-8">
      <div className="w-full max-w-2xl space-y-4">
        {/* ACP 批改入口(主操作) */}
        <div className="rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-wider text-primary">{questionName}</p>
          <h2 className="mt-1.5 text-xl font-semibold">{t('启动 Codex 自动批改')}</h2>
          {isLoading ? (
            <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />{t('加载助手目录中...')}</div>
          ) : codexReady ? (
            <>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t('点击后会在右侧 AI 助手中填入批改指令;你先调整模型、思考强度与权限档位,再手动发送开始批改。')}</p>
              <Button className="mt-4" onClick={() => onStartInAssistant?.()}><Play className="size-4" />{t('开始批改')}</Button>
              <p className="mt-3 text-xs text-muted-foreground">{t('沙箱与网络限制由 Codex 原生配置决定，应用不再叠加限制。')}</p>
            </>
          ) : (
            <p className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-700">{t('请先在系统设置安装并连接测试 Codex ACP。')}</p>
          )}
        </div>

        {/* MCP 恢复兜底 */}
        <div className="rounded-2xl border border-slate-200 bg-muted/40 p-6">
          <McpWaitingPanel
            questionName={questionName ?? null}
            questionId={questionId ?? null}
          />
        </div>
      </div>
    </div>
  );
}

function AcpFinishedPanel({ run }: { run: AcpRun }) {
  const { t } = useLanguage();
  const description = run.status === 'completed' ? t('评分建议已保存,页面将自动进入复核。') : run.status === 'failed' ? (run.error_message ?? t('批改运行失败,可重新发起。')) : t('批改运行已取消,可重新发起。');
  return <div className="flex h-full items-center justify-center p-8"><div className="w-full max-w-lg rounded-2xl border border-primary/15 bg-white p-6 text-center shadow-sm"><h2 className="text-lg font-semibold">{t(runStatusLabel(run.status))}</h2><p className="mt-2 text-sm text-muted-foreground">{description}</p></div></div>;
}
