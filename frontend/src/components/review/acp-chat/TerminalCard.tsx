import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Loader2, X } from 'lucide-react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useLanguage } from '@/i18n';
import { CopyButton } from './CopyButton';

export type ToolStatus = 'running' | 'completed' | 'failed';

/**
 * 命令执行终端卡:头部为状态灯 + $ 命令 + 时长/复制/展开操作,
 * 输出区为浅灰等宽块,带状态色条;运行中末尾显示闪烁光标。
 * 默认收起为单行保持转录紧凑,运行中自动展开,结束后保持展开。
 */

const STATUS_ACCENT: Record<ToolStatus, string> = {
  completed: 'bg-emerald-500',
  failed: 'bg-red-400',
  running: 'bg-primary animate-pulse',
};

function ToolStatusIcon({ status }: { status: ToolStatus }) {
  if (status === 'completed') {
    return <Check className="size-3.5 shrink-0 text-emerald-500" aria-hidden />;
  }
  if (status === 'failed') {
    return <X className="size-3.5 shrink-0 text-red-400" aria-hidden />;
  }
  return <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden />;
}

/** 运行时长展示:running 时走秒,结束(完成/失败)后冻结。 */
function ElapsedBadge({ status, startedAt }: { status: ToolStatus; startedAt?: number }) {
  const [endedAt, setEndedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const running = status === 'running' && startedAt !== undefined;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  // 离开 running 的瞬间冻结结束时刻。
  useEffect(() => {
    if (status !== 'running' && startedAt !== undefined && endedAt === null) {
      setEndedAt(Date.now());
    }
  }, [status, startedAt, endedAt]);

  if (startedAt === undefined) return null;
  const end = running ? now : (endedAt ?? now);
  const seconds = Math.max(0, (end - startedAt) / 1000);
  const label = seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
  return (
    <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70">
      {label}
    </span>
  );
}

export function TerminalCard({
  command,
  status,
  output,
  toolKind,
  startedAtMs,
  seq,
}: {
  command: string;
  status: ToolStatus;
  output: string;
  /** ACP 工具类别:仅 execute(命令执行)显示 $ 提示符 */
  toolKind?: string;
  /** 工具开始时间(ms 时间戳),用于时长展示;缺失时不显示 */
  startedAtMs?: number;
  seq: number;
}) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(status === 'running');
  const outputRef = useRef<HTMLPreElement>(null);

  const hasOutput = output.trim().length > 0;
  const isCommand = toolKind === 'execute';
  // 运行中自动展开;结束后保持展开让教师看到结果(手动收起后不强行再开)。
  useEffect(() => {
    if (status === 'running') setOpen(true);
  }, [status]);

  // 运行中输出增长时贴底滚动。
  useEffect(() => {
    if (status === 'running' && open) {
      outputRef.current?.scrollTo({ top: outputRef.current.scrollHeight });
    }
  }, [output, status, open]);

  const statusLabel =
    status === 'completed'
      ? t('已完成')
      : status === 'failed'
        ? t('失败')
        : t('运行中');

  return (
    <div
      className="shrink-0 overflow-hidden rounded-xl border border-border/70 bg-card shadow-sm transition-colors"
      data-testid={`terminal-card-${seq}`}
    >
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`${statusLabel}: ${command}`}
        className="flex min-w-0 cursor-pointer select-none items-center gap-2 px-2.5 py-2 text-xs transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setOpen((v) => !v);
          }
        }}
      >
        <ToolStatusIcon status={status} />
        {isCommand ? (
          <span className="shrink-0 font-mono text-muted-foreground/60" aria-hidden>
            $
          </span>
        ) : null}
        <span className="min-w-0 flex-1 break-all font-mono leading-relaxed text-foreground/90">
          {command}
        </span>
        <span
          className={cn('size-1.5 shrink-0 rounded-full', STATUS_ACCENT[status])}
          aria-hidden
        />
        <ElapsedBadge status={status} startedAt={startedAtMs} />
        {hasOutput ? <CopyButton text={output} label={t('复制输出')} /> : null}
        <CopyButton text={command} label={t('复制命令')} />
        <ChevronDown
          className={cn(
            'size-3.5 shrink-0 text-muted-foreground/70 transition-transform',
            open && 'rotate-180',
          )}
          aria-hidden
        />
      </div>
      {open && hasOutput ? (
        <div className="relative border-t border-border/60">
          <span
            className={cn('absolute inset-y-0 left-0 w-0.5', STATUS_ACCENT[status])}
            aria-hidden
          />
          <pre
            ref={outputRef}
            className="max-h-64 overflow-auto whitespace-pre-wrap break-words bg-muted/50 py-2 pl-3.5 pr-3 font-mono text-[11px] leading-relaxed text-foreground/80"
          >
            {output}
            {status === 'running' ? <span className="terminal-caret" aria-hidden /> : null}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
