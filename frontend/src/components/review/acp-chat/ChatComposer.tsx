import { type KeyboardEvent, type ReactNode } from 'react';
import { Send, Square } from 'lucide-react';
import { Textarea } from '@/components/ui/textarea';
import { useLanguage } from '@/i18n';

/**
 * 对话输入卡(Zed / ChatGPT 式):一张大圆角卡片,placeholder 在卡片顶部,
 * 底部一行放工具控件与黑色圆形发送按钮。full 变体铺满面板(新对话空态),
 * docked 变体停靠在转录下方。
 *
 * 文本受控:由面板持有草稿,「开始批改」等入口可把指令直接写进输入框,
 * 教师在此基础上编辑后再手动发送。
 */
export function ChatComposer({
  value,
  onChange,
  disabled,
  isSending,
  running = false,
  onSend,
  onStop,
  placeholder,
  toolbarControls,
  variant = 'docked',
}: {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  isSending: boolean;
  /** 回复中/等待批准时输入禁用,发送按钮切换为停止 */
  running?: boolean;
  onSend: (text: string) => void;
  onStop?: () => void;
  placeholder?: string;
  /** 工具行控件(权限/模型下拉),位于发送按钮旁 */
  toolbarControls?: ReactNode;
  /** full=新对话空态铺满面板; docked=停靠转录底部 */
  variant?: 'full' | 'docked';
}) {
  const { t } = useLanguage();

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled || isSending || running) return;
    onSend(trimmed);
    onChange('');
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div
      className={
        variant === 'full'
          ? // 卡片保持自然高度、锚定底部,上方留白(参考 ChatGPT/Zed 输入卡)
            'flex min-h-0 flex-1 flex-col justify-end px-3 pb-3'
          : 'shrink-0 px-3 pb-3'
      }
    >
      <div className="flex flex-col rounded-2xl border border-border/70 bg-card shadow-sm transition-colors focus-within:border-border">
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={variant === 'full' ? 2 : 1}
          autoFocus={variant === 'full'}
          placeholder={placeholder ?? t('输入消息,Enter 发送,Shift+Enter 换行')}
          className={`resize-none border-0 bg-transparent px-4 text-sm leading-relaxed shadow-none focus-visible:ring-0 ${
            variant === 'full' ? 'max-h-48 min-h-0 overflow-y-auto py-3.5' : 'max-h-32 min-h-11 py-3'
          }`}
          aria-label={t('消息输入框')}
        />
        <div className="flex min-w-0 flex-wrap items-center gap-1 px-2.5 pb-2 pt-0.5">
          {/* 单行收缩:工具栏压缩吸收宽度,发送按钮始终钉在右侧 */}
          <div className="flex min-w-0 flex-1 items-center justify-end gap-0.5">
            {toolbarControls}
          </div>
          {running && onStop ? (
            <button
              type="button"
              onClick={onStop}
              aria-label={t('停止')}
              className="btn-press flex size-9 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-opacity hover:opacity-85"
            >
              <Square className="size-3.5" />
            </button>
          ) : (
            <button
              type="button"
              disabled={disabled || isSending || running || value.trim().length === 0}
              onClick={submit}
              aria-label={isSending ? t('发送中') : t('发送')}
              className="btn-press flex size-9 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-opacity hover:opacity-85 disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground"
            >
              <Send className="size-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
