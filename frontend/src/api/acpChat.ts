import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import { queryKeys } from './queryKeys';
import type {
  AcpConfigSnapshot,
  AcpPermissionMode,
  CodexConfigRequest,
  CodexConfigurationCatalog,
  CodexSelection,
} from './acp';

// ACP 对话面板:会话生命周期、消息、权限裁决与 SSE 事件流前端接口。

export type AcpChatStatus = 'idle' | 'running' | 'waiting_permission' | 'closed' | 'error';

export interface AcpChatSession {
  id: number;
  submission_id: number;
  agent_id: string;
  permission_mode: AcpPermissionMode;
  codex_config: AcpConfigSnapshot;
  applied_codex_config: AcpConfigSnapshot | null;
  pending_codex_config: AcpConfigSnapshot | null;
  effective_at: 'current' | 'next_turn';
  status: AcpChatStatus;
  last_error: string | null;
  latest_seq: number;
  created_at: string;
  updated_at: string;
}

export type { CodexConfigurationCatalog, CodexSelection };

// 目录查询走 @/api/acp 的 useCodexConfiguration(同一查询缓存)。

export interface AcpChatSessionList {
  sessions: AcpChatSession[];
}

export interface AcpChatEvent {
  seq: number;
  kind: string;
  payload: { text?: string; title?: string; status?: string; [k: string]: unknown };
  created_at: string;
}

export function useAcpChatSessions(submissionId: number | undefined) {
  return useQuery<AcpChatSessionList>({
    queryKey: queryKeys.acp.chat.sessions(submissionId),
    queryFn: () =>
      apiClient
        .get<AcpChatSessionList>('/acp/chat/sessions', {
          params: { submission_id: submissionId },
        })
        .then((r) => r.data),
    enabled: submissionId !== undefined,
  });
}

export function useCreateAcpChatSession(submissionId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<
    AcpChatSession,
    Error,
    { permission_mode: AcpPermissionMode; codex_config: CodexConfigRequest }
  >({
    mutationFn: (body) =>
      apiClient
        .post<AcpChatSession>('/acp/chat/sessions', { submission_id: submissionId, ...body })
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.acp.chat.sessions(data.submission_id),
      });
    },
  });
}

export function useAcpChatSession(chatId: number | undefined) {
  return useQuery<AcpChatSession>({
    queryKey: queryKeys.acp.chat.session(chatId),
    queryFn: () =>
      apiClient
        .get<AcpChatSession>(`/acp/chat/sessions/${chatId}`, {
          // 404 是「会话已被删除」的正常信号,轮询中反复弹错误 toast 会误导教师
          skipErrorToast: true,
        })
        .then((r) => r.data),
    enabled: chatId !== undefined,
    retry: (failureCount, error) => {
      const status = (error as { response?: { status?: number } }).response?.status;
      return status !== 404 && failureCount < 2;
    },
  });
}

/** 已有会话热更新模型/思考度/权限档位;回复中修改排队到下回合生效。 */
export function useUpdateChatConfiguration(chatId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<
    AcpChatSession,
    Error,
    {
      permission_mode?: AcpPermissionMode;
      codex_config?: CodexConfigRequest;
    }
  >({
    mutationFn: (body) =>
      apiClient
        .patch<AcpChatSession>(`/acp/chat/sessions/${chatId}/codex-configuration`, body)
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.chat.all });
    },
  });
}

/** 发送一条消息;服务端在 turn 结束前保持 running,409 表示上一条仍在处理。 */
export function useSendChatMessage(chatId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<AcpChatSession, Error, string>({
    mutationFn: (text) =>
      apiClient
        .post<AcpChatSession>(`/acp/chat/sessions/${chatId}/messages`, { text })
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.chat.all });
    },
  });
}

export function useChatPermission(chatId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<
    { status: string; allowed: boolean },
    Error,
    { permission_id: string; allow: boolean; option_id?: string | null }
  >({
    mutationFn: (body) =>
      apiClient
        .post(`/acp/chat/sessions/${chatId}/permission`, body)
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.chat.all });
    },
  });
}

export function useCancelChatTurn(chatId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<{ status: string; cancelled: boolean }, Error, void>({
    mutationFn: () =>
      apiClient.post(`/acp/chat/sessions/${chatId}/cancel`).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.chat.all });
    },
  });
}

/** 关闭会话(终态,不可再发消息);会话仍保留供历史查看。 */
export function useCloseChatSession(submissionId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<{ status: string }, Error, number>({
    mutationFn: (chatId) =>
      apiClient.delete(`/acp/chat/sessions/${chatId}`).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.chat.sessions(submissionId) });
    },
  });
}

/** 永久删除对话:删除会话记录、事件转录与专属工作区,不可恢复。 */
export function useDeleteChatSessionPermanent() {
  const queryClient = useQueryClient();
  return useMutation<{ status: string }, Error, number>({
    mutationFn: (chatId) =>
      apiClient.delete(`/acp/chat/sessions/${chatId}/permanent`).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.chat.all });
    },
  });
}

const CHAT_EVENT_LIMIT = 200;

/**
 * SSE 订阅对话事件流;先由服务端回放历史,再增量推送。
 * 返回全部事件(上限 200 条)供转录渲染;会话终态时由前端调用方停止订阅。
 */
export function useAcpChatEvents(chatId: number | undefined, enabled: boolean) {
  const [events, setEvents] = useState<AcpChatEvent[]>([]);
  const [caughtUp, setCaughtUp] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (chatId === undefined || !enabled) return;
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') return;
    setEvents([]);
    setCaughtUp(false);
    const es = new EventSource(`/api/acp/chat/sessions/${chatId}/stream`);
    es.addEventListener('chat_event', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as AcpChatEvent;
        setEvents((prev) => {
          if (prev.length > 0 && prev[prev.length - 1].seq >= data.seq) return prev;
          const next = [...prev, data];
          return next.length > CHAT_EVENT_LIMIT
            ? next.slice(next.length - CHAT_EVENT_LIMIT)
            : next;
        });
        setCaughtUp(true);
        // 会话状态(等待权限/回合结束)随事件变化:轻量刷新会话详情
        queryClient.invalidateQueries({ queryKey: queryKeys.acp.chat.session(chatId) });
      } catch {
        // 忽略非法事件
      }
    });
    es.onerror = null;
    return () => {
      es.close();
    };
  }, [chatId, enabled, queryClient]);

  return { events, caughtUp };
}

/** 从事件流提取最近一个未答复的权限请求(最新优先)。 */
export function findPendingPermission(events: AcpChatEvent[]): {
  permission_id: string;
  kind: string;
  title: string;
  options: { option_id: string; kind: string | null; title: string }[];
} | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i];
    if (e.kind !== 'permission_request') continue;
    const payload = e.payload as {
      permission_id?: string;
      kind?: string;
      title?: string;
      options?: { option_id?: string; kind?: string | null; title?: string }[];
    };
    const pid = payload.permission_id ?? '';
    if (!pid) continue;
    const resolved = events
      .slice(i + 1)
      .some(
        (later) =>
          later.kind === 'permission_resolved' &&
          (later.payload as { permission_id?: string }).permission_id === pid,
      );
    if (resolved) continue;
    return {
      permission_id: pid,
      kind: payload.kind ?? '',
      title: payload.title ?? '',
      options: (payload.options ?? []).map((o) => ({
        option_id: o.option_id ?? '',
        kind: o.kind ?? null,
        title: o.title ?? '',
      })),
    };
  }
  return null;
}
