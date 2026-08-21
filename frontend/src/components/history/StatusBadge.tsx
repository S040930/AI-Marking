import { isProcessing, type SubmissionStatus } from '@/api/submissions';
import { Badge } from '@/components/ui/badge';
import { useLanguage } from '@/i18n';
import { STATUS_TEXT } from '@/lib/submissionStatus';

export function StatusBadge({ status }: { status: SubmissionStatus }) {
  const { t } = useLanguage();
  const label = t(STATUS_TEXT[status]);
  if (status === 'reviewed') {
    return (
      <Badge className="border border-success/20 bg-success/10 text-success hover:bg-success/15">
        {label}
      </Badge>
    );
  }
  if (status === 'failed') {
    return <Badge variant="destructive">{label}</Badge>;
  }
  if (status === 'ready_for_review') {
    return (
      <Badge className="border border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100">
        {label}
      </Badge>
    );
  }
  if (status === 'awaiting_mcp') {
    return <Badge variant="outline">{label}</Badge>;
  }
  if (isProcessing(status)) {
    return (
      <Badge
        variant="secondary"
        className="gap-1.5 pr-2.5 text-primary"
      >
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping motion-reduce:animate-none rounded-full bg-primary opacity-75" />
          <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
        </span>
        {label}
      </Badge>
    );
  }
  return <Badge variant="outline">{label}</Badge>;
}
