import { useState } from 'react';
import { Check, ChevronDown, Hand, Loader2, Zap } from 'lucide-react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { useLanguage } from '@/i18n';
import type {
  AcpPermissionMode,
  CodexConfigurationCatalog,
  CodexSelection,
} from '@/api/acp';
import { cn } from '@/lib/utils';

/**
 * 对话输入卡底部工具栏(Zed / ChatGPT 式):左侧手型权限下拉,右侧
 * Fast mode 圆形开关(切换需成本确认,锚定在开关上的 Popover 内完成)
 * 与「模型 + 思考强度」合并下拉。新对话草稿态与已有会话共用:
 * 已有会话的切换由面板层 PATCH 热更新(回复中排队下回合生效)。
 */

const triggerClass =
  'flex min-w-0 items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-50';

const shrinkTriggerClass =
  triggerClass + ' [&>span:first-child]:truncate [&>span:first-child]:basis-0 [&>span:first-child]:grow';

function MenuList({
  label,
  options,
  value,
  onSelect,
}: {
  label: string;
  options: { id: string; label: string; hint?: string }[];
  value: string;
  onSelect: (id: string) => void;
}) {
  return (
    <ul role="listbox" aria-label={label}>
      {options.map((option) => (
        <li key={option.id}>
          <button
            type="button"
            role="option"
            aria-selected={option.id === value}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted"
            onClick={() => onSelect(option.id)}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{option.label}</span>
              {option.hint ? (
                <span className="block truncate text-[11px] text-muted-foreground">
                  {option.hint}
                </span>
              ) : null}
            </span>
            {option.id === value ? (
              <Check className="size-3.5 shrink-0 text-primary" />
            ) : null}
          </button>
        </li>
      ))}
    </ul>
  );
}

function PermissionDropdown({
  value,
  disabled,
  onSelect,
}: {
  value: AcpPermissionMode;
  disabled?: boolean;
  onSelect: (mode: AcpPermissionMode) => void;
}) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const label = value === 'auto_review' ? t('自动批准') : t('请求批准');
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={triggerClass}
        disabled={disabled}
        aria-label={t('权限档位')}
        aria-expanded={open}
      >
        <Hand className="size-3.5 shrink-0" />
        <span className="truncate">{label}</span>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-48 p-1">
        <MenuList
          label={t('权限档位')}
          value={value}
          options={[
            { id: 'ask', label: t('请求批准') },
            { id: 'auto_review', label: t('自动批准') },
          ]}
          onSelect={(id) => {
            onSelect(id as AcpPermissionMode);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}

export function ModelEffortDropdown({
  catalog,
  selection,
  disabled,
  onSelect,
}: {
  catalog: CodexConfigurationCatalog;
  selection: CodexSelection;
  disabled?: boolean;
  onSelect: (selection: CodexSelection) => void;
}) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const modelOptions = catalog.models.map((option) => ({
    id: option.id,
    label: option.label,
    hint: option.current ? t('当前默认') : undefined,
  }));
  const reasoningOptions = catalog.reasoning_efforts.map((option) => ({
    id: option.id,
    label: option.label,
  }));

  const selectedModel = catalog.models.find((option) => option.id === selection.modelId);
  const selectedReasoning = catalog.reasoning_efforts.find(
    (option) => option.id === selection.reasoningEffort,
  );
  const modelLabel = selectedModel?.label ?? t('Agent 默认模型');
  const effortLabel = selectedReasoning?.label ?? t('Agent 默认思考');

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={shrinkTriggerClass}
        disabled={disabled}
        aria-label={t('模型与思考强度')}
        aria-expanded={open}
      >
        <span className="truncate font-medium text-foreground/80">{modelLabel}</span>
        <span className="shrink-0">{effortLabel}</span>
        <ChevronDown className="size-3 shrink-0 opacity-70" />
      </PopoverTrigger>
      <PopoverContent align="end" className="max-h-80 w-56 overflow-y-auto p-1">
        <p className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('Codex 模型')}
        </p>
        <MenuList
          label={t('Codex 模型')}
          value={selection.modelId ?? ''}
          options={
            modelOptions.length > 0
              ? modelOptions
              : [{ id: '', label: t('Agent 默认模型') }]
          }
          onSelect={(id) => {
            onSelect({ ...selection, modelId: id || null });
            setOpen(false);
          }}
        />
        <div className="mx-2 my-1 border-t border-border/60" />
        <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('思考强度')}
        </p>
        <MenuList
          label={t('思考强度')}
          value={selection.reasoningEffort ?? ''}
          options={
            reasoningOptions.length > 0
              ? reasoningOptions
              : [{ id: '', label: t('Agent 默认思考') }]
          }
          onSelect={(id) => {
            onSelect({ ...selection, reasoningEffort: id || null });
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}

export function FastModeToggle({
  checked,
  disabled,
  disabledReason,
  confirming,
  onConfirmingChange,
  onToggle,
  onConfirm,
  onCancel,
}: {
  checked: boolean;
  disabled?: boolean;
  disabledReason?: string;
  confirming: boolean;
  onConfirmingChange: (open: boolean) => void;
  onToggle: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useLanguage();
  const knob = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={t('Fast mode')}
      disabled={disabled}
      onClick={onToggle}
      title={disabled ? disabledReason : undefined}
      className={cn(
        'flex size-6 shrink-0 items-center justify-center rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-40',
        checked
          ? 'border-transparent bg-primary text-primary-foreground'
          : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      <Zap className="size-3" />
    </button>
  );

  if (disabled) {
    return knob;
  }

  return (
    <Popover open={confirming} onOpenChange={onConfirmingChange}>
      <PopoverTrigger asChild>{knob}</PopoverTrigger>
      <PopoverContent align="end" className="w-64 p-3" role="dialog" aria-label={t('确认快速模式')}>
        <p className="text-xs font-medium">{t('确认快速模式')}</p>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          {t('快速模式可能提高 ChatGPT 额度或 API 成本。')}
        </p>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            className="btn-press inline-flex items-center rounded-md bg-primary px-2.5 py-1 text-xs text-primary-foreground"
            onClick={onConfirm}
          >
            <Check className="size-3" /> {t('确认快速模式')}
          </button>
          <button
            type="button"
            className="rounded-md border border-border px-2.5 py-1 text-xs"
            onClick={onCancel}
          >
            {t('取消')}
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function ChatDraftToolbar({
  catalog,
  catalogFetching,
  permissionMode,
  selection,
  disabled,
  onPermissionModeChange,
  onSelectionChange,
}: {
  catalog: CodexConfigurationCatalog;
  catalogFetching?: boolean;
  permissionMode: AcpPermissionMode;
  selection: CodexSelection;
  disabled?: boolean;
  onPermissionModeChange: (mode: AcpPermissionMode) => void;
  onSelectionChange: (selection: CodexSelection) => void;
}) {
  const { t } = useLanguage();
  const [confirmFastOpen, setConfirmFastOpen] = useState(false);

  const fast = catalog.speed_modes.find((option) => option.id === 'fast');
  const fastAvailable = Boolean(fast?.available);
  const isFast = selection.speedMode === 'fast';

  return (
    <div
      className="flex min-w-0 flex-1 items-center gap-0.5"
      data-testid="chat-draft-toolbar"
    >
      <PermissionDropdown
        value={permissionMode}
        disabled={disabled}
        onSelect={onPermissionModeChange}
      />
      <div className="ml-auto flex min-w-0 items-center justify-end gap-0.5">
        <FastModeToggle
          checked={isFast}
          disabled={Boolean(disabled) || !fastAvailable}
          disabledReason={
            fastAvailable ? undefined : t('当前账户、模型或 Agent 版本未声明快速模式，暂仅支持标准模式。')
          }
          confirming={confirmFastOpen}
          onConfirmingChange={setConfirmFastOpen}
          onToggle={() => {
            if (isFast) {
              onSelectionChange({ ...selection, speedMode: 'standard' });
            } else {
              // 切到 fast 先开确认 Popover;确认后 commit,取消保持 standard。
              setConfirmFastOpen(true);
            }
          }}
          onConfirm={() => {
            setConfirmFastOpen(false);
            onSelectionChange({ ...selection, speedMode: 'fast' });
          }}
          onCancel={() => {
            setConfirmFastOpen(false);
            onSelectionChange({ ...selection, speedMode: 'standard' });
          }}
        />
        <ModelEffortDropdown
          catalog={catalog}
          selection={selection}
          disabled={disabled}
          onSelect={onSelectionChange}
        />
        {catalogFetching ? (
          <Loader2
            className="size-3 shrink-0 animate-spin text-muted-foreground"
            aria-label={t('读取 Codex 配置能力中…')}
          />
        ) : null}
      </div>
    </div>
  );
}
