import { Badge } from '@/components/ui/badge';
import type { SubmissionDetail } from '@/api/submissions';
import { useLanguage } from '@/i18n';

/**
 * 评分来源审计条:展示建议的生成方(内嵌 agent / 历史兼容)与 rubric 来源。
 *
 * 元数据读取 `agent_metadata`;旧数据(v12 及更早)为 `mcp_metadata`,
 * 由 schema 层桥接到同一字段。
 */
export function AgentAuditBanner({ data }: { data: SubmissionDetail }) {
  const { t } = useLanguage();
  const metadata = data.assessment_suggestion?.agent_metadata;
  const source = metadata?.source;
  const sourceLabel = source === 'mcp' ? t('外部助手(历史)') : t('AI 批改');
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
      <Badge variant="secondary">{sourceLabel}</Badge>
      <span className="text-muted-foreground">revision {data.grading_revision}</span>
      {rubricSourceLabel && (
        <Badge variant="outline" className="text-muted-foreground">
          rubric: {t(rubricSourceLabel)}
        </Badge>
      )}
    </div>
  );
}
