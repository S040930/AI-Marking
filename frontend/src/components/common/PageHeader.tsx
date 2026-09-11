import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageHeaderProps {
  title: string;
  description?: string;
  /** 右侧操作区（如记录数、按钮组） */
  actions?: ReactNode;
  /**
   * 追加到外层容器的类名。间距（如 mb-6）与对齐方式（items-end / items-center /
   * flex-col sm:flex-row）由调用方按页面原样传入，组件不预设。
   */
  className?: string;
}

export function PageHeader({
  title,
  description,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <div className={cn('flex justify-between gap-4', className)}>
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  );
}
