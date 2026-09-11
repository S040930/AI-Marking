import { useNavigate } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';
import { cn } from '@/lib/utils';

/** 返回历史记录页的按钮，在批改页与结果页多处复用。 */
export function BackToHistoryButton({ className }: { className?: string }) {
  const navigate = useNavigate();
  const { t } = useLanguage();

  return (
    <Button
      variant="ghost"
      onClick={() => navigate('/history')}
      className={cn('-ml-2 text-muted-foreground hover:text-foreground', className)}
    >
      <ChevronLeft />
      {t('返回历史')}
    </Button>
  );
}
