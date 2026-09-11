import { Loader2 } from 'lucide-react';
import { useLanguage } from '@/i18n';
import { cn } from '@/lib/utils';

export function PageLoading({
  text,
  className,
}: {
  text?: string;
  className?: string;
}) {
  const { t } = useLanguage();

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 py-32',
        className,
      )}
    >
      <Loader2 className="size-8 animate-spin text-primary" />
      <p className="text-sm text-muted-foreground">{text ?? t('加载中...')}</p>
    </div>
  );
}
