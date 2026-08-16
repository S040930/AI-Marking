import { afterEach, describe, expect, it, vi } from 'vitest';
import { AUTH_CHANGED_EVENT, login, logout, notifyAuthChanged } from '@/lib/auth';

describe('login', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('令牌通过时返回 true', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 200 })),
    );
    expect(await login('tok-123')).toBe(true);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe('/api/auth/login');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({ token: 'tok-123' });
  });

  it('令牌被拒时返回 false', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
    );
    expect(await login('wrong')).toBe(false);
  });

  it('网络异常时返回 false', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    expect(await login('tok-123')).toBe(false);
  });
});

describe('logout', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('请求 /api/auth/logout', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 200 })),
    );
    expect(await logout()).toBe(true);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/auth/logout');
  });
});

describe('notifyAuthChanged', () => {
  it('派发 AUTH_CHANGED_EVENT 事件', () => {
    const listener = vi.fn();
    window.addEventListener(AUTH_CHANGED_EVENT, listener);
    notifyAuthChanged();
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_CHANGED_EVENT, listener);
  });
});