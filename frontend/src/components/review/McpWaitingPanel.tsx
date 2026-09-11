import { useState } from 'react';
import { toast } from 'sonner';
import { Copy, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';
import { buildMcpResumePrompt } from '@/lib/gradingPrompt';

/**
 * MCP 恢复兜底:复制恢复指令,让外部编程助手(如 Codex)经 MCP 评分。
 *
 * ``variant="section"``(默认)渲染为嵌入父卡片的小节,不带自己的外壳;
 * ``variant="card"`` 渲染为独立卡片(供单独页面使用)。
 * 提示词以题目名(而非作业编号)定位,与题目库的批改提示词同源。
 */
export function McpWaitingPanel({
  questionName,
  questionId,
  variant = 'section',
}: {
  questionName: string | null;
  questionId: string | null;
  variant?: 'section' | 'card';
}) {
  const { locale, t } = useLanguage();
  const [copied, setCopied] = useState(false);
  // 数据层保证 submission 必关联题目(question_id 非空外键);若字段缺失则不渲染
  if (!questionName || !questionId) return null;
  const prompt = buildMcpResumePrompt({ questionName, questionId }, locale);

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

  if (variant === 'card') {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="w-full max-w-lg rounded-2xl border border-primary/15 bg-white p-6 shadow-sm">
          <McpWaitingSection
            questionName={questionName}
            prompt={prompt}
            copied={copied}
            onCopy={copyPrompt}
          />
        </div>
      </div>
    );
  }

  return (
    <McpWaitingSection
      questionName={questionName}
      prompt={prompt}
      copied={copied}
      onCopy={copyPrompt}
    />
  );
}

function McpWaitingSection({
  questionName,
  prompt,
  copied,
  onCopy,
}: {
  questionName: string;
  prompt: string;
  copied: boolean;
  onCopy: () => void;
}) {
  const { t } = useLanguage();
  return (
    <div>
      <h3 className="text-sm font-semibold text-foreground">{t('等待编程助手评分')}</h3>
      <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
        {t('这条作业的 OCR 已完成，正等待 MCP 客户端（如 Codex）评分。如果原任务已关闭，可复制下面的恢复指令继续处理，或使用待办列表工具发现作业。')}
      </p>
      <div className="mt-3 flex items-start gap-2">
        <pre className="min-w-0 flex-1 whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-xs leading-relaxed text-foreground">
          {prompt}
        </pre>
        <Button variant="outline" size="sm" className="shrink-0" onClick={onCopy}>
          {copied ? <Check /> : <Copy />}
          {copied ? t('已复制') : t('复制编程助手指令')}
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        {t('编程助手只会保存评分建议；最终成绩仍需教师回到此网页确认。')}
      </p>
      <span className="sr-only">{t('题目')} {questionName} · {t('等待 MCP 评分')}</span>
    </div>
  );
}
