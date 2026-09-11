import { useEffect, useMemo, useRef } from 'react';
import { AlertTriangle, Info } from 'lucide-react';
import type { AcpChatEvent } from '@/api/acpChat';
import { cn } from '@/lib/utils';
import { MarkdownContent } from './MarkdownContent';
import { TerminalCard, type ToolStatus } from './TerminalCard';

type Block =
  | { type: 'user'; text: string; seq: number }
  | { type: 'assistant'; text: string; seq: number }
  | {
      type: 'tool';
      title: string;
      toolKind: string;
      status: ToolStatus;
      output: string;
      startedAtMs?: number;
      seq: number;
    }
  | { type: 'notice'; text: string; tone: 'info' | 'error'; seq: number };

const _TOOL_EVENT_KINDS = new Set(['tool_started', 'tool_finished']);

/**
 * 把事件流折叠成块:用户消息开组,文本增量累积到上一个 assistant 块,
 * 工具调用按 tool_call_id 合并为单卡(started 卡随后续 finished 更新状态与输出)。
 */
function buildBlocks(events: AcpChatEvent[]): Block[] {
  const blocks: Block[] = [];
  const toolIndexById = new Map<string, number>();
  for (const event of events) {
    if (event.kind === 'user_message') {
      blocks.push({
        type: 'user',
        text: String(event.payload.text ?? ''),
        seq: event.seq,
      });
      continue;
    }
    if (event.kind === 'agent_message_chunk' || event.kind === 'message_delta') {
      const text = String(event.payload.text ?? '');
      if (!text) continue;
      const last = blocks[blocks.length - 1];
      if (last?.type === 'assistant') {
        last.text += text;
      } else {
        blocks.push({ type: 'assistant', text, seq: event.seq });
      }
      continue;
    }
    if (_TOOL_EVENT_KINDS.has(event.kind)) {
      const id = String(event.payload.tool_call_id ?? '');
      const existing = id ? toolIndexById.get(id) : undefined;
      if (existing !== undefined && blocks[existing]?.type === 'tool') {
        // 同一工具调用的后续更新:合并进已有卡
        const tool = blocks[existing];
        if (tool.type !== 'tool') continue;
        const title = String(event.payload.title ?? '');
        if (title) tool.title = title;
        tool.status = toToolStatus(event.payload.status, event.kind === 'tool_finished');
        const toolKind = String(event.payload.kind ?? '');
        if (toolKind) tool.toolKind = toolKind;
        const output = String(event.payload.output ?? '');
        if (output) tool.output = output;
        continue;
      }
      const status = toToolStatus(event.payload.status, event.kind === 'tool_finished');
      toolIndexById.set(id || `seq:${event.seq}`, blocks.length);
      blocks.push({
        type: 'tool',
        title: String(event.payload.title ?? ''),
        toolKind: String(event.payload.kind ?? ''),
        status,
        output: String(event.payload.output ?? ''),
        startedAtMs: toTimestampMs(event.created_at),
        seq: event.seq,
      });
      continue;
    }
    if (event.kind === 'notice') {
      blocks.push({
        type: 'notice',
        text: String(event.payload.text ?? ''),
        tone: 'info',
        seq: event.seq,
      });
      continue;
    }
    if (event.kind === 'error') {
      blocks.push({
        type: 'notice',
        text: String(event.payload.text ?? ''),
        tone: 'error',
        seq: event.seq,
      });
    }
    // permission_request/resolved、turn_completed 等由面板层渲染
  }
  return blocks;
}

function toToolStatus(raw: unknown, finished: boolean): ToolStatus {
  const value = String(raw ?? '');
  if (finished || value === 'completed') return 'completed';
  if (value === 'failed') return 'failed';
  return 'running';
}

/** created_at(ISO 或毫秒)→ ms 时间戳;解析失败返回 undefined。 */
function toTimestampMs(value: string): number | undefined {
  if (!value) return undefined;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? undefined : parsed;
}

function NoticeBanner({ tone, text }: { tone: 'info' | 'error'; text: string }) {
  const error = tone === 'error';
  return (
    <div
      className={cn(
        'flex shrink-0 items-start gap-2 rounded-xl px-3 py-2 text-xs leading-relaxed',
        error
          ? 'bg-destructive/8 text-destructive'
          : 'bg-muted/60 text-muted-foreground',
      )}
      role={error ? 'alert' : 'status'}
    >
      {error ? (
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      ) : (
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      )}
      <span className="min-w-0 flex-1 break-words">{text}</span>
    </div>
  );
}

/** 对话转录:用户消息右对齐浅靛蓝气泡,助手回复平铺,工具调用为终端卡。 */
export function ChatTranscript({ events }: { events: AcpChatEvent[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const blocks = useMemo(() => buildBlocks(events), [events]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [events.length]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
      {blocks.map((block) => {
        if (block.type === 'user') {
          return (
            <div
              key={block.seq}
              className="ml-auto max-w-[85%] shrink-0 whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary/10 px-3.5 py-2.5 text-sm leading-relaxed text-foreground"
            >
              {block.text}
            </div>
          );
        }
        if (block.type === 'assistant') {
          return (
            <div key={block.seq} className="min-w-0 shrink-0">
              <MarkdownContent text={block.text} />
            </div>
          );
        }
        if (block.type === 'tool') {
          return (
            <TerminalCard
              key={block.seq}
              seq={block.seq}
              command={block.title}
              status={block.status}
              output={block.output}
              toolKind={block.toolKind || undefined}
              startedAtMs={block.startedAtMs}
            />
          );
        }
        return <NoticeBanner key={block.seq} tone={block.tone} text={block.text} />;
      })}
      <div ref={bottomRef} />
    </div>
  );
}
