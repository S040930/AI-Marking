import { useEffect, useState, type FormEvent } from 'react';
import { apiClient } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Lock } from 'lucide-react';
import { AUTH_CHANGED_EVENT, login } from '@/lib/auth';
import { useLanguage } from '@/i18n';

interface AuthGateProps {
  children: React.ReactNode;
}

/**
 * 全站访问令牌门。后端鉴权关闭（ACCESS_TOKEN 为空）时 /api/auth/verify
 * 恒返回 200，本组件直接放行，不改变原有使用方式；开启时校验会话
 * Cookie，无效则渲染输入卡片。令牌只在登录门提交一次，后端以
 * httpOnly Cookie 维持会话，前端不存储令牌。
 */
export default function AuthGate({ children }: AuthGateProps) {
  const { t } = useLanguage();
  const [status, setStatus] = useState<'checking' | 'verified' | 'failed'>(
    'checking',
  );
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const verify = async () => {
    setStatus('checking');
    try {
      await apiClient.get('/auth/verify', { skipErrorToast: true });
      setStatus('verified');
    } catch {
      setStatus('failed');
    }
  };

  // 挂载时探测会话 Cookie；401 拦截器在会话失效时通过事件重新进入登录门。
  useEffect(() => {
    void verify();
    const onChange = () => setStatus('failed');
    window.addEventListener(AUTH_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const value = input.trim();
    if (!value) return;
    setSubmitting(true);
    setError('');
    try {
      const ok = await login(value);
      if (!ok) {
        throw new Error('invalid token');
      }
      setInput('');
      setStatus('verified');
    } catch {
      setError(t('访问令牌无效，请检查 backend/.env 中的 ACCESS_TOKEN'));
      setStatus('failed');
    } finally {
      setSubmitting(false);
    }
  };

  if (status === 'checking') {
    return <div className="flex min-h-screen items-center justify-center" />;
  }

  if (status === 'verified') {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50/70 px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <div className="flex size-9 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Lock className="size-4" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-foreground">{t('访问令牌')}</h1>
            <p className="text-xs text-muted-foreground">
              {t('请输入 backend/.env 中的 ACCESS_TOKEN')}
            </p>
          </div>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            type="password"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t('访问令牌')}
            autoFocus
            disabled={submitting}
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
          <Button
            type="submit"
            className="w-full"
            disabled={submitting || !input.trim()}
          >
            {submitting ? t('验证中…') : t('进入')}
          </Button>
        </form>
      </div>
    </div>
  );
}
