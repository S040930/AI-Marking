// 全站访问令牌的浏览器侧会话管理。
// 令牌本身只经登录门提交一次，后端以 httpOnly Cookie 维持会话
// （HttpOnly 使 JS 无法读取，SameSite=Lax 防跨站携带），前端不存储
// 令牌，也不再把令牌拼进 URL（避免进入浏览器历史/服务器日志/Referer）。

// 会话失效（Cookie 丢失/过期，如 401 响应）时通知 AuthGate 重新进入登录门。
export const AUTH_CHANGED_EVENT = 'ai-marking-auth-changed';

export function notifyAuthChanged(): void {
  try {
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  } catch {
    // SSR/jsdom 等环境无 window 时静默忽略
  }
}

// 登录门提交令牌：校验通过后后端 Set-Cookie（httpOnly + SameSite=Lax），
// 此后所有同源请求（含 SSE/PDF 下载等原生请求）自动携带 Cookie。
// 用原生 fetch 避免与 apiClient 的 401 拦截器循环依赖。
export async function login(token: string): Promise<boolean> {
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// 清除会话 Cookie，AuthGate 据此回到登录门。
export async function logout(): Promise<boolean> {
  try {
    const res = await fetch('/api/auth/logout', { method: 'POST' });
    return res.ok;
  } catch {
    return false;
  }
}