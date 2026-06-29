import axios, { type AxiosError } from 'axios';
import { QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

interface ApiErrorResponse {
  detail?: string;
}

/**
 * 扩展 axios config，支持通过 meta.skipErrorToast 跳过拦截器的自动 toast。
 * 用于 mutation 场景：页面 onError 自行处理提示，避免重复 toast。
 */
declare module 'axios' {
  export interface AxiosRequestConfig {
    skipErrorToast?: boolean;
  }
}

/**
 * 从 AxiosError 中提取面向用户的友好错误信息。
 * 优先取 FastAPI 标准 detail 字段，回退到 error.message。
 */
function resolveErrorMessage(error: AxiosError<ApiErrorResponse>): string {
  const status = error.response?.status;
  const detail = error.response?.data?.detail;

  // 无响应（网络错误 / 超时）
  if (!error.response) {
    if (error.code === 'ECONNABORTED') {
      return '请求超时，请检查网络后重试';
    }
    return '网络连接异常，请检查网络后重试';
  }

  // 按状态码分类给出语义化文案
  if (status === 401) {
    return '登录已过期，请重新登录';
  }
  if (status === 403) {
    return '没有权限执行此操作';
  }
  if (status === 404) {
    return detail ?? '请求的资源不存在';
  }
  if (status && status >= 500) {
    return detail ?? '服务器异常，请稍后重试';
  }

  return detail ?? error.message ?? '请求失败，请重试';
}

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorResponse>) => {
    const message = resolveErrorMessage(error);
    // 完整 error 对象记录到控制台便于调试，toast 仅展示友好信息
    console.error('[API Error]', error);

    // 通过 config.skipErrorToast 标记跳过自动 toast（mutation 由页面 onError 处理）
    if (!error.config?.skipErrorToast) {
      toast.error(message);
    }

    return Promise.reject(error);
  },
);

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      retryDelay: 1000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      // mutation 错误由各页面 onError 处理，拦截器通过 skipErrorToast 跳过自动 toast
      retry: 0,
    },
  },
});
