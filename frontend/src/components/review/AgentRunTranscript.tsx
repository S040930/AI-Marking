import { MAX_AGENT_EVENTS, type AgentRunEvent } from '@/api/agent';
import { useLanguage } from '@/i18n';
import { cn } from '@/lib/utils';

/**
 * 转录事件在 UI 侧的上限,防止长 run 拖慢渲染(与 SSE 侧保留上限一致)。
 * 事件按产出它的 agent 分组:主管分支与各评分项分支分别成组并带徽标。
 */
export function AgentTranscript({ events }: { events: AgentRunEvent[] }) {
  const { t } = useLanguage();
  const visible = events.slice(-MAX_AGENT_EVENTS);
  const groups = groupEventsByAgent(visible);
  if (groups.length === 0) return null;
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t('执行转录')}
        </h3>
        {events.length > visible.length ? (
          <span className="text-[10px] text-slate-400">
            {t('仅显示最近 300 条事件')}
          </span>
        ) : null}
      </div>
      <div className="mt-2 max-h-72 space-y-3 overflow-y-auto">
        {groups.map((group) => (
          <section key={group.agent}>
            <span
              data-testid="agent-badge"
              className={cn(
                'inline-flex items-center rounded-md px-1.5 py-0.5 font-mono text-[10px]',
                group.agent === 'supervisor'
                  ? 'bg-primary/10 text-primary'
                  : 'bg-slate-100 text-slate-600',
              )}
            >
              {agentLabel(group.agent, t)}
            </span>
            <ul className="mt-1 space-y-1.5 text-sm">
              {group.events.map((event) => (
                <li key={event.seq} className="leading-relaxed text-slate-700">
                  <span className="mr-1.5 font-mono text-[10px] uppercase text-slate-400">
                    {event.kind}
                  </span>
                  {eventText(event)}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

function eventText(event: AgentRunEvent): string {
  return (
    (event.payload.text as string | undefined) ??
    (event.payload.title as string | undefined) ??
    ''
  );
}

/** 按产出事件的 agent 分组(保持首次出现顺序);无文本事件不展示。 */
function groupEventsByAgent(events: AgentRunEvent[]) {
  const groups = new Map<string, AgentRunEvent[]>();
  for (const event of events) {
    if (!eventText(event)) continue;
    const agent = (event.payload.agent as string | undefined) ?? 'supervisor';
    const bucket = groups.get(agent);
    if (bucket) bucket.push(event);
    else groups.set(agent, [event]);
  }
  return [...groups.entries()].map(([agent, grouped]) => ({
    agent,
    events: grouped,
  }));
}

/** agent 徽标文案:`item:<rubric_item_id>` 显示为「评分项 <id>」。 */
function agentLabel(agent: string, t: (key: string) => string): string {
  const ITEM_PREFIX = 'item:';
  if (agent.startsWith(ITEM_PREFIX)) {
    return `${t('评分项')} ${agent.slice(ITEM_PREFIX.length)}`;
  }
  return agent === 'supervisor' ? t('主管') : agent;
}
