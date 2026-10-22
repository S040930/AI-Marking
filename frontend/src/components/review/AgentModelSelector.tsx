import { useAgentProviders, type AgentProviderName } from '@/api/agent';
import { useLanguage } from '@/i18n';

export function AgentModelSelector({
  provider,
  defaultModel,
  value,
  currentModel,
  compact = false,
  onChange,
}: {
  provider: AgentProviderName;
  defaultModel: string;
  value: string;
  currentModel?: string | null;
  compact?: boolean;
  onChange: (model: string) => void;
}) {
  const { t } = useLanguage();
  const { modelDiscovery, modelsLoading } = useAgentProviders(true, provider);
  const models = modelDiscovery?.status === 'ready' ? modelDiscovery.models : [];
  const options = [...new Set([...models, currentModel ?? ''])].filter(
    (model) => model && model !== defaultModel,
  );
  const unavailable = modelDiscovery && modelDiscovery.status !== 'ready';
  return (
    <label
      className={`flex flex-col gap-1 text-muted-foreground ${compact ? 'min-w-0 flex-1 text-[11px]' : 'text-xs'}`}
    >
      {t('模型')}
      <select
        className={compact
          ? 'h-8 min-w-0 rounded-lg border border-slate-200 bg-white px-2 text-xs text-foreground outline-none focus:ring-2 focus:ring-primary/30'
          : 'h-9 rounded-lg border border-slate-200 bg-white px-2.5 text-sm outline-none focus:ring-2 focus:ring-primary/30'}
        value={value === defaultModel ? '' : value}
        aria-label={t('模型 ID')}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">
          {defaultModel ? `${t('默认模型')} · ${defaultModel}` : t('默认模型')}
        </option>
        {options.map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
      </select>
      {modelsLoading ? (
        <span className={compact ? undefined : 'text-[11px]'}>
          {t('正在加载模型列表...')}
        </span>
      ) : unavailable ? (
        <span className={compact ? 'text-amber-700' : 'text-[11px] text-amber-700'}>
          {modelDiscovery.detail ?? t('模型列表暂不可用，将使用默认模型。')}
        </span>
      ) : null}
    </label>
  );
}
