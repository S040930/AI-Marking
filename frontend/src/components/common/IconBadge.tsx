import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** 卡片/区块标题前的渐变图标徽章，在设置页多处复用。 */
export function IconBadge({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10',
        className,
      )}
    >
      {children}
    </div>
  );
}
