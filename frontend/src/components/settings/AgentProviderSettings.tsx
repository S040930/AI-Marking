import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  BadgeCheck,
  FlaskConical,
  KeyRound,
  Loader2,
  LogIn,
  LogOut,
  Save,
} from 'lucide-react';
import {
  useAgentProviders,
  useChatGptLoginStatus,
  useLogoutChatGpt,
  useSetDefaultProvider,
  useStartChatGptLogin,
  useTestProvider,
  useUpdateProviderConfig,
  type AgentProviderName,
} from '@/api/agent';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useLanguage } from '@/i18n';
import { IconBadge } from '@/components/common/IconBadge';

const EFFORT_OPTIONS = ['', 'minimal', 'low', 'medium', 'high'] as const;

/**
 * 模型通道设置:官方 OpenAI API(主通道)与 ChatGPT 订阅登录。
 *
 * 密钥只写不读:本页永不回显已保存的 key,只显示后四位;
 * 订阅令牌由后端 OAuth 流程落库,前端只轮询状态。
 */
export function AgentProviderSettings() {
  const { t } = useLanguage();
  const { data, isLoading } = useAgentProviders();
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [defaultModel, setDefaultModel] = useState('');
  const [effort, setEffort] = useState('');
  const [loginPending, setLoginPending] = useState(false);
  const updateMutation = useUpdateProviderConfig();
  const testMutation = useTestProvider();
  const defaultMutation = useSetDefaultProvider();
  const startLogin = useStartChatGptLogin();
  const logoutMutation = useLogoutChatGpt();
  const loginStatus = useChatGptLoginStatus(loginPending);

  const openai = data?.providers['openai_api'];
  const subscription = data?.providers['chatgpt_sub'];
  const providerDefault = (data?.provider_default ?? 'openai_api') as AgentProviderName;
  const loginState = loginStatus.data?.status;
  const loginDetail = loginStatus.data?.detail;

  // 服务端数据到达后回灌表单(api_key 除外:只写不读)。
  useEffect(() => {
    if (!openai) return;
    setBaseUrl(openai.base_url);
    setDefaultModel(openai.default_model);
    setEffort(openai.reasoning_effort);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openai?.base_url, openai?.default_model, openai?.reasoning_effort]);

  const save = () => {
    const config: {
      api_key?: string | null;
      base_url?: string | null;
      default_model?: string | null;
      reasoning_effort?: string | null;
    } = {};
    if (apiKey.trim()) config.api_key = apiKey.trim();
    if (baseUrl !== (openai?.base_url ?? '')) config.base_url = baseUrl.trim();
    if (defaultModel !== (openai?.default_model ?? '')) {
      config.default_model = defaultModel.trim();
    }
    if (effort !== (openai?.reasoning_effort ?? '')) {
      config.reasoning_effort = effort;
    }
    if (Object.keys(config).length === 0) {
      toast.info(t('配置没有变化'));
      return;
    }
    updateMutation.mutate(
      { name: 'openai_api', config },
      {
        onSuccess: () => {
          setApiKey('');
          toast.success(t('通道配置已保存'));
        },
        onError: (err) => toast.error(err.message),
      },
    );
  };

  const test = () => {
    testMutation.mutate('openai_api', {
      onSuccess: (result) => {
        if (result.status === 'ready') {
          toast.success(
            t('连接正常') +
              (result.models.length > 0 ? `（${result.models.length} ${t('个可用模型')}）` : ''),
          );
        } else {
          toast.error(result.detail ?? t('连接测试未通过'));
        }
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const setDefault = (name: AgentProviderName) => {
    defaultMutation.mutate(name, {
      onSuccess: () => toast.success(t('默认通道已更新')),
      onError: (err) => toast.error(err.message),
    });
  };

  // 登录结果由轮询驱动:完成/失败各提示一次,并停止轮询。
  useEffect(() => {
    if (!loginPending) return;
    if (loginState === 'completed') {
      setLoginPending(false);
      toast.success(t('ChatGPT 订阅登录成功'));
    } else if (loginState === 'failed') {
      setLoginPending(false);
      toast.error(loginDetail ?? t('登录失败，请重试'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loginPending, loginState, loginDetail]);

  const startChatGptLogin = () => {
    startLogin.mutate(undefined, {
      onSuccess: (result) => {
        window.open(result.authorize_url, '_blank', 'noopener,noreferrer');
        setLoginPending(true);
        toast.info(t('已打开浏览器，请完成 ChatGPT 登录'));
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const logoutChatGpt = () => {
    logoutMutation.mutate(undefined, {
      onSuccess: () => toast.success(t('已退出 ChatGPT 订阅')),
      onError: (err) => toast.error(err.message),
    });
  };

  return (
    <div className="mb-5 flex flex-col gap-5">
      {/* 官方 OpenAI API */}
      <Card className="elevated-card animate-fade-in-up motion-reduce:animate-none overflow-hidden">
        <CardHeader className="pb-4">
          <div className="flex items-center gap-3">
            <IconBadge>
              <KeyRound className="size-5" />
            </IconBadge>
            <div>
              <CardTitle className="text-lg">{t('官方 OpenAI API')}</CardTitle>
              <CardDescription>
                {t('批改 agent 的主通道：使用 API key 直接调用 OpenAI Responses API')}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-5">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">API Key</span>
            <Input
              type="password"
              autoComplete="new-password"
              placeholder={
                openai?.configured
                  ? `${t('已配置')} ····${openai.key_last4 ?? ''}（${t('留空表示不修改')}）`
                  : t('请输入 OpenAI API key')
              }
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <span className="text-xs text-muted-foreground">
              {openai?.configured
                ? `${t('当前已配置')} ····${openai.key_last4 ?? ''}`
                : t('尚未配置')}
            </span>
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">{t('API 地址(可选)')}</span>
            <Input
              placeholder="https://api.openai.com/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">{t('默认模型')}</span>
              <Input
                placeholder="gpt-5"
                value={defaultModel}
                onChange={(e) => setDefaultModel(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">{t('默认思考强度')}</span>
              <select
                className="h-9 rounded-lg border border-slate-200 bg-white px-2.5 text-sm outline-none focus:ring-2 focus:ring-primary/30"
                value={effort}
                aria-label={t('默认思考强度')}
                onChange={(e) => setEffort(e.target.value)}
              >
                {EFFORT_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option || t('模型默认')}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button disabled={updateMutation.isPending} onClick={save}>
              {updateMutation.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
              {t('保存通道配置')}
            </Button>
            <Button
              variant="outline"
              disabled={testMutation.isPending}
              onClick={test}
            >
              {testMutation.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <FlaskConical className="size-4" />
              )}
              {t('连接测试')}
            </Button>
            {testMutation.data?.status === 'ready' ? (
              <span className="flex items-center gap-1 text-xs text-emerald-600">
                <BadgeCheck className="size-3.5" />
                {t('连接正常')}
              </span>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {/* ChatGPT 订阅登录 */}
      <Card className="elevated-card animate-fade-in-up motion-reduce:animate-none overflow-hidden">
        <CardHeader className="pb-4">
          <div className="flex items-center gap-3">
            <IconBadge>
              <FlaskConical className="size-5" />
            </IconBadge>
            <div>
              <CardTitle className="text-lg">{t('ChatGPT 订阅通道')}</CardTitle>
              <CardDescription>
                {t('使用 ChatGPT 订阅额度调用模型；通过 ChatGPT OAuth 登录授权。')}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-5">
          {subscription?.available ? (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3">
              <p className="flex items-center gap-1.5 text-sm font-medium text-emerald-700">
                <BadgeCheck className="size-4" />
                {t('已登录')}
                {subscription.plan ? ` · ${subscription.plan}` : ''}
                {subscription.account_last4
                  ? ` · ${t('账号')} ····${subscription.account_last4}`
                  : ''}
              </p>
              {subscription.expires_at ? (
                <p className="mt-1 text-xs text-emerald-800/70">
                  {t('访问令牌有效至')}{' '}
                  {new Date(subscription.expires_at).toLocaleString()} ·{' '}
                  {t('到期自动刷新')}
                </p>
              ) : null}
            </div>
          ) : (
            <p className="rounded-xl bg-muted/60 p-3 text-xs leading-relaxed text-muted-foreground">
              {t('尚未登录。登录后可在评审页选择该通道，使用订阅额度批改。')}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {subscription?.available ? (
              <Button
                variant="outline"
                disabled={logoutMutation.isPending}
                onClick={logoutChatGpt}
              >
                {logoutMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <LogOut className="size-4" />
                )}
                {t('退出登录')}
              </Button>
            ) : (
              <Button
                disabled={startLogin.isPending || loginPending}
                onClick={startChatGptLogin}
              >
                {startLogin.isPending || loginPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <LogIn className="size-4" />
                )}
                {t('登录 ChatGPT')}
              </Button>
            )}
            {loginPending ? (
              <span className="text-xs text-muted-foreground">
                {t('等待浏览器完成登录…')}
              </span>
            ) : null}
          </div>

          <p className="rounded-xl bg-muted/60 p-3 text-xs leading-relaxed text-muted-foreground">
            {t(
              '订阅通道依赖非公开接口，存在账号风控风险；如遇限流或接口变动，可随时切回官方 API 通道。',
            )}
          </p>
        </CardContent>
      </Card>

      {/* 默认通道 */}
      <Card className="elevated-card animate-fade-in-up motion-reduce:animate-none overflow-hidden">
        <CardHeader className="pb-4">
          <CardTitle className="text-lg">{t('默认批改通道')}</CardTitle>
          <CardDescription>
            {t('发起批改时未指定通道则使用默认通道')}
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-5">
          <div className="grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              aria-pressed={providerDefault === 'openai_api'}
              onClick={() => setDefault('openai_api')}
              className={`rounded-xl border p-3 text-left transition-colors ${
                providerDefault === 'openai_api'
                  ? 'border-primary bg-primary/5'
                  : 'border-slate-200 bg-white hover:border-primary/40'
              }`}
            >
              <p className="text-sm font-medium">{t('官方 OpenAI API')}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {openai?.configured ? t('已配置') : t('未配置')}
              </p>
            </button>
            <button
              type="button"
              aria-pressed={providerDefault === 'chatgpt_sub'}
              disabled={!subscription?.available}
              onClick={() => setDefault('chatgpt_sub')}
              className={`rounded-xl border p-3 text-left transition-colors ${
                providerDefault === 'chatgpt_sub'
                  ? 'border-primary bg-primary/5'
                  : 'border-slate-200 bg-white hover:border-primary/40'
              } ${subscription?.available ? '' : 'cursor-not-allowed opacity-60'}`}
            >
              <p className="text-sm font-medium">{t('ChatGPT 订阅')}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {subscription?.available
                  ? subscription.plan || t('已登录')
                  : t('未登录')}
              </p>
            </button>
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <p className="text-xs text-muted-foreground">{t('加载通道配置中...')}</p>
      ) : null}
    </div>
  );
}
