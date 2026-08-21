import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';

interface CopyablePromptPanelProps {
  prompt: string;
}

export function CopyablePromptPanel({ prompt }: CopyablePromptPanelProps) {
  const { t } = useLanguage();
  const [copied, setCopied] = useState(false);

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
      toast.success(t('已复制'));
    } catch {
      toast.error(t('复制失败，请手动复制上方文本'));
    }
  };

  return (
    <div className="space-y-3">
      <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-700">
        {prompt}
      </pre>
      <Button size="sm" onClick={copyPrompt}>
        {copied ? <Check /> : <Copy />}
        {copied ? t('已复制') : t('复制')}
      </Button>
    </div>
  );
}
