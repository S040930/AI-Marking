import { useEffect, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * 轻量复制按钮:写入系统剪贴板,成功后短暂显示对勾反馈。
 * 供终端卡与 Markdown 代码块复用;jsdom 等无剪贴板环境下静默失败。
 * text 可传函数(如从 DOM ref 读取),在点击时才求值。
 */
export function CopyButton({
  text,
  label,
  className,
}: {
  text: string | (() => string);
  /** 无障碍标签(i18n 后的文案,如「复制命令」) */
  label: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    const value = typeof text === 'function' ? text() : text;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      // 剪贴板不可用(非安全上下文/无权限)时静默忽略,不打断阅读。
    }
  };

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30',
        className,
      )}
      onClick={(event) => {
        event.stopPropagation();
        void copy();
      }}
    >
      {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
    </button>
  );
}
