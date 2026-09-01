import { Badge } from '@/components/ui/badge';
import type { SubmissionDetail } from '@/api/submissions';
import { useLanguage } from '@/i18n';
import { CLIENT_LABELS } from '@/lib/mcpClients';

export function McpAuditBanner({ data }: { data: SubmissionDetail }) {
  const { t } = useLanguage();
  const metadata = data.assessment_suggestion?.mcp_metadata;
  const client = metadata?.client;
  const clientLabel = client ? CLIENT_LABELS[client] ?? client : null;
  const rubricSource = metadata?.rubric_source;
  const rubricSourceLabel =
      rubricSource === 'question_extracted'
      ? '题目提取'
      : rubricSource === 'configured'
        ? '配置项'
        : rubricSource === 'built_in_default'
          ? '内置默认'
          : null;
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-primary/10 bg-primary/[0.03] px-5 py-3 text-xs">
      <Badge variant="secondary">{clientLabel ?? t('MCP 客户端')}</Badge>
      <span className="text-muted-foreground">revision {data.grading_revision}</span>
      {rubricSourceLabel && (
        <Badge variant="outline" className="text-muted-foreground">
          rubric: {t(rubricSourceLabel)}
        </Badge>
      )}
    </div>
  );
}
