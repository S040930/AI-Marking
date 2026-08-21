import { useState } from 'react';
import { toast } from 'sonner';
import { Copy, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';

export function McpWaitingPanel({ submissionId }: { submissionId: number }) {
  const { locale, t } = useLanguage();
  const [copied, setCopied] = useState(false);
  const prompt = `${t('请继续使用 AI-Marking 批改作业')} #${submissionId}${locale === 'zh-CN' ? '。' : '.'}`;

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
      toast.success(t('编程助手指令已复制'));
    } catch {
      toast.error(t('复制失败，请手动选择指令'));
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-lg rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-wider text-primary">
          {t('作业')} #{submissionId} · {t('等待 MCP 评分')}
        </p>
        <h2 className="mt-2 text-xl font-semibold">{t('等待编程助手评分')}</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {t('这条作业的 OCR 已完成，正等待 MCP 客户端（如 Codex）评分。如果原任务已关闭，可复制下面的恢复指令继续处理，或使用待办列表工具发现作业。')}
        </p>
        <div className="mt-5 rounded-xl bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">
          {prompt}
        </div>
        <Button className="mt-4" onClick={copyPrompt}>
          {copied ? <Check /> : <Copy />}
          {copied ? t('已复制') : t('复制编程助手指令')}
        </Button>
        <p className="mt-4 text-xs text-muted-foreground">
          {t('编程助手只会保存评分建议；最终成绩仍需教师回到此网页确认。')}
        </p>
      </div>
    </div>
  );
}
