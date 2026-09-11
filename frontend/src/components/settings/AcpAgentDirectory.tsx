import { useState } from 'react';
import { toast } from 'sonner';
import {
  Bot,
  Check,
  CheckCircle2,
  Clock,
  Download,
  HelpCircle,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react';
import {
  type AcpAgent,
  type AcpConnectionStatus,
  useAcpAgents,
  useInstallAcpAgent,
  useRefreshAcpRegistry,
  useTestAcpAgent,
  useUninstallAcpAgent,
} from '@/api/acp';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';
import { IconBadge } from '@/components/common/IconBadge';

const CONNECTION_BADGE: Record<
  AcpConnectionStatus,
  { labelKey: string; className: string } | null
> = {
  ready: {
    labelKey: '已连接',
    className: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  },
  needs_auth: {
    labelKey: '待登录',
    className: 'bg-amber-100 text-amber-700 border-amber-200',
  },
  failed: {
    labelKey: '连接失败',
    className: 'bg-red-100 text-red-700 border-red-200',
  },
  unsupported: {
    labelKey: '不支持',
    className: 'bg-slate-100 text-slate-500 border-slate-200',
  },
  unknown: null,
};

export function AcpAgentDirectory() {
  const { t } = useLanguage();
  const { data, isLoading } = useAcpAgents();
  const refreshMutation = useRefreshAcpRegistry();
  const installMutation = useInstallAcpAgent();
  const testMutation = useTestAcpAgent();
  const uninstallMutation = useUninstallAcpAgent();
  // 正在测试的 agent;测试是异步握手,用本地状态提示"测试中"。
  const [testingId, setTestingId] = useState<string | null>(null);
  const [installedId, setInstalledId] = useState<string | null>(null);

  const busy =
    installMutation.isPending ||
    testMutation.isPending ||
    uninstallMutation.isPending;

  const handleTest = (agentId: string) => {
    setTestingId(agentId);
    testMutation.mutate(agentId, {
      onSuccess: (result) => {
        setTestingId(null);
        if (result.status === 'ready') {
          toast.success(t('连接测试通过'));
        } else if (result.status === 'needs_auth') {
          toast.warning(t('助手需要先在本机登录'));
        } else {
          toast.error(result.detail || t('连接测试失败'));
        }
      },
      onError: (err) => {
        setTestingId(null);
        toast.error(err.message);
      },
    });
  };

  const handleInstall = (agentId: string) => {
    installMutation.mutate(agentId, {
      onSuccess: (result) => {
        setInstalledId(agentId);
        window.setTimeout(() => setInstalledId(null), 1800);
        toast.success(
          result.reinstalled
            ? `${t('已安装版本')} ${result.version}`
            : `${t('版本已是最新')}: ${result.version}`,
        );
      },
      onError: (err) => toast.error(err.message),
    });
  };

  /** 卸载已安装链接:只删安装标记与下载缓存,agent 仍留在目录中。 */
  const handleUninstall = (agentId: string) => {
    if (!window.confirm(t('确认卸载该助手？已安装链接将被删除，助手仍保留在目录中。')))
      return;
    uninstallMutation.mutate(agentId, {
      onSuccess: () => toast.success(t('已卸载安装链接')),
      onError: (err) => toast.error(err.message),
    });
  };

  return (
    <Card className="elevated-card mb-5">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <IconBadge>
              <Bot className="size-5" />
            </IconBadge>
            <div>
              <CardTitle className="text-lg">{t('批改助手目录')}</CardTitle>
              <CardDescription>
                {t('Codex ACP 安装与连接测试；连接后可发起自动批改')}
              </CardDescription>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                refreshMutation.mutate(undefined, {
                  onSuccess: () => toast.success(t('Registry 已刷新')),
                  onError: (err) => toast.error(err.message),
                })
              }
              disabled={refreshMutation.isPending}
            >
              {refreshMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <RefreshCw className="size-4" />
              )}
              {t('刷新')}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-1">
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {t('加载助手目录中...')}
          </div>
        ) : !data || data.agents.filter((agent) => agent.agent_id === 'codex-acp').length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {t('Registry 暂不可用，且没有本地缓存目录')}
          </p>
        ) : (
          <div className="flex flex-col divide-y">
            {data.agents.filter((agent) => agent.agent_id === 'codex-acp').map((agent) => (
              <AgentRow
                key={agent.agent_id}
                agent={agent}
                isTesting={testingId === agent.agent_id}
                justInstalled={installedId === agent.agent_id}
                busy={busy}
                onInstall={() => handleInstall(agent.agent_id)}
                onTest={() => handleTest(agent.agent_id)}
                onUninstall={() => handleUninstall(agent.agent_id)}
              />
            ))}
          </div>
        )}
        {data?.registry_version ? (
          <p className="mt-3 text-xs text-muted-foreground">
            {t('Registry 版本')}: {data.registry_version}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AgentRow({
  agent,
  isTesting,
  justInstalled,
  busy,
  onInstall,
  onTest,
  onUninstall,
}: {
  agent: AcpAgent;
  isTesting: boolean;
  justInstalled: boolean;
  busy: boolean;
  onInstall: () => void;
  onTest: () => void;
  onUninstall: () => void;
}) {
  const { t } = useLanguage();
  const badge = CONNECTION_BADGE[agent.connection_status];
  const hasUpdate =
    agent.installed_version !== null &&
    agent.available_version !== null &&
    agent.installed_version !== agent.available_version;

  return (
    <div className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{agent.display_name}</span>
          <Badge
            variant="outline"
            className="gap-1 border-slate-200 bg-slate-50 text-slate-600"
          >
            <ShieldCheck className="size-3" />
            {t('Codex 原生工作区沙箱，网络已关闭')}
          </Badge>
          {badge ? (
            <Badge variant="outline" className={`gap-1 ${badge.className}`}>
              {agent.connection_status === 'ready' ? (
                <CheckCircle2 className="size-3" />
              ) : agent.connection_status === 'needs_auth' ? (
                <Clock className="size-3" />
              ) : (
                <XCircle className="size-3" />
              )}
              {t(badge.labelKey)}
            </Badge>
          ) : null}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {agent.whitelist_package}
          {agent.installed_version
            ? ` · ${t('已装')} ${agent.installed_version}`
            : ` · ${t('未安装')}`}
          {hasUpdate ? ` · ${t('可更新到')} ${agent.available_version}` : ''}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {agent.installed_version ? (
          <>
            <Button variant="outline" size="sm" onClick={onTest} disabled={busy}>
              {isTesting ? <Loader2 className="size-4 animate-spin" /> : null}
              {t('测试连接')}
            </Button>
            {hasUpdate ? (
              <Button variant="outline" size="sm" onClick={onInstall} disabled={busy}>
                <Download className="size-4" />
                {t('更新')}
              </Button>
            ) : null}
          </>
        ) : (
          <Button variant="outline" size="sm" onClick={onInstall} disabled={busy}>
            {justInstalled ? (
              <Check className="size-4" />
            ) : (
              <Download className="size-4" />
            )}
            {agent.available_version
              ? t('安装')
              : t('安装（Registry 无版本信息）')}
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-8 text-muted-foreground hover:text-red-600"
          aria-label={t('卸载')}
          title={agent.installed_version ? t('卸载已安装链接') : t('尚未安装')}
          onClick={onUninstall}
          disabled={busy || !agent.installed_version}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </div>
  );
}

export function AcpAgentDirectoryHint() {
  const { t } = useLanguage();
  return (
    <p className="mb-5 flex items-center gap-1.5 text-xs text-muted-foreground">
      <HelpCircle className="size-3.5" />
      {t('登录凭证由各助手自行管理；连接测试不会保存任何密钥。')}
    </p>
  );
}
