import { Loader2, ShieldCheck, ShieldX } from 'lucide-react';
import { toast } from 'sonner';
import type { useChatPermission } from '@/api/acpChat';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';

export interface PendingPermission {
  permission_id: string;
  kind: string;
  title: string;
  options: { option_id: string; kind: string | null; title: string }[];
}

type PermissionMutation = ReturnType<typeof useChatPermission>;

/** 权限裁决卡:助手请求执行工具时,教师批准(第一个 allow 选项)或拒绝。 */
export function PermissionCard({
  request,
  mutation,
}: {
  request: PendingPermission;
  mutation: PermissionMutation;
}) {
  const { t } = useLanguage();
  const allowOption = request.options.find(
    (o) => o.kind !== null && o.kind.startsWith('allow'),
  );

  return (
    <div className="mx-4 mb-3 shrink-0 rounded-2xl border border-warning/30 bg-warning/10 p-3.5">
      <div className="flex items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-lg bg-warning/20">
          <ShieldCheck className="size-3.5 shrink-0 text-warning-foreground" />
        </span>
        <h3 className="text-sm font-semibold text-warning-foreground">{t('助手请求批准')}</h3>
      </div>
      <p className="mt-1.5 break-all text-sm leading-relaxed text-warning-foreground/90">
        {request.title || request.kind || t('未知的工具操作')}
      </p>
      {request.options.length > 0 ? (
        <p className="mt-1 text-xs text-warning-foreground/70">
          {request.options.map((o) => o.title || o.option_id).join(' / ')}
        </p>
      ) : null}
      <div className="mt-3 flex gap-2">
        <Button
          size="sm"
          disabled={mutation.isPending}
          onClick={() =>
            mutation.mutate(
              {
                permission_id: request.permission_id,
                allow: true,
                option_id: allowOption?.option_id || undefined,
              },
              { onError: (err) => toast.error(err.message) },
            )
          }
        >
          {mutation.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <ShieldCheck className="size-4" />
          )}
          {t('批准')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={mutation.isPending}
          className="border-warning/40 text-warning-foreground hover:bg-warning/20"
          onClick={() =>
            mutation.mutate(
              { permission_id: request.permission_id, allow: false },
              { onError: (err) => toast.error(err.message) },
            )
          }
        >
          <ShieldX className="size-4" />
          {t('拒绝')}
        </Button>
      </div>
    </div>
  );
}
