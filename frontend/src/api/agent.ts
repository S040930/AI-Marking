import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import { queryKeys } from './queryKeys';

// 批改 agent:run 生命周期与模型通道配置前端接口。

export type AgentRunStatus =
  | 'queued'
  | 'starting'
  | 'running'
  | 'waiting_for_teacher'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type AgentProviderName = 'openai_api' | 'chatgpt_sub';

export interface AgentCheckpoint {
  type: string;
  message: string;
  asked_at: string | null;
  expires_at: string | null;
}

export interface AgentRun {
  id: number;
  submission_id: number;
  provider: string;
  model: string | null;
  reasoning_effort: string | null;
  effective_at: 'current' | 'next_turn';
  status: AgentRunStatus;
  checkpoint: AgentCheckpoint | null;
  teacher_verdict: 'consistent' | 'mismatch' | null;
  teacher_note: string | null;
  error_message: string | null;
  attempts: number;
  max_attempts: number;
  usage: Record<string, unknown> | null;
  created_at: string;
  finished_at: string | null;
}

export interface AgentRunDetail {
  run: AgentRun;
  latest_seq: number;
  applied_config: Record<string, unknown> | null;
  pending_config: Record<string, unknown> | null;
}

export interface AgentRunEvent {
  seq: number;
  kind: string;
  payload: { text?: string; title?: string; status?: string; [k: string]: unknown };
  created_at: string;
}

export interface AgentProviderStatus {
  configured: boolean;
  key_last4: string | null;
  base_url: string;
  default_model: string;
  reasoning_effort: string;
  available: boolean;
}

export interface AgentProviderList {
  provider_default: string;
  providers: Record<string, AgentProviderStatus>;
}

export interface AgentProviderTestResult {
  name: string;
  status: 'ready' | 'needs_auth' | 'unsupported' | 'failed';
  detail: string | null;
  models: string[];
}

export interface AgentProviderConfigUpdate {
  api_key?: string | null;
  base_url?: string | null;
  default_model?: string | null;
  reasoning_effort?: string | null;
}

/** 模型通道列表(设置页与开始批改面板共用)。 */
export function useAgentProviders(enabled = true) {
  return useQuery<AgentProviderList>({
    queryKey: queryKeys.agent.providers,
    queryFn: () => apiClient.get<AgentProviderList>('/agent/providers').then((r) => r.data),
    retry: 1,
    enabled,
  });
}

export function useSetDefaultProvider() {
  const queryClient = useQueryClient();
  return useMutation<AgentProviderList, Error, AgentProviderName>({
    mutationFn: (provider) =>
      apiClient
        .put<AgentProviderList>('/agent/providers/default', { provider })
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agent.providers, data);
    },
  });
}

export function useUpdateProviderConfig() {
  const queryClient = useQueryClient();
  return useMutation<
    AgentProviderList,
    Error,
    { name: AgentProviderName; config: AgentProviderConfigUpdate }
  >({
    mutationFn: ({ name, config }) =>
      apiClient
        .put<AgentProviderList>(`/agent/providers/${name}/config`, config)
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agent.providers, data);
    },
  });
}

/** 连接测试:有效 key 时同时返回可用模型列表。 */
export function useTestProvider() {
  return useMutation<AgentProviderTestResult, Error, AgentProviderName>({
    mutationFn: (name) =>
      apiClient
        .post<AgentProviderTestResult>(`/agent/providers/${name}/test`)
        .then((r) => r.data),
  });
}

/** 查询某作业的活跃 run(404 视为无 run,不弹错误 toast)。 */
export function useActiveAgentRun(submissionId: number | undefined) {
  return useQuery<AgentRunDetail>({
    queryKey: queryKeys.agent.run(submissionId),
    queryFn: () =>
      apiClient
        .get<AgentRunDetail>(`/agent/runs/by-submission/${submissionId}`, {
          // 404 是「无活跃 run」的正常信号,轮询中反复弹错误 toast 会误导教师
          skipErrorToast: true,
        })
        .then((r) => r.data),
    enabled: submissionId !== undefined,
    retry: (failureCount, error) => {
      const status = (error as { response?: { status?: number } }).response?.status;
      return status !== 404 && failureCount < 2;
    },
  });
}

export interface CreateAgentRunInput {
  provider?: AgentProviderName;
  model?: string | null;
  reasoning_effort?: string | null;
}

export function useCreateAgentRun(submissionId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<AgentRunDetail, Error, CreateAgentRunInput>({
    mutationFn: (body) =>
      apiClient
        .post<AgentRunDetail>('/agent/runs', {
          submission_id: submissionId,
          ...body,
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agent.run(data.run.submission_id), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.all });
    },
  });
}

/**
 * 运行中热切换模型/思考强度。
 * 后端按 run 当前状态决定生效时机:未启动的 run 立即生效,进行中的 run
 * 排队到下一回合(响应里 effective_at === 'next_turn')。
 */
export function useUpdateAgentRunConfiguration(runId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<
    AgentRunDetail,
    Error,
    { model?: string | null; reasoning_effort?: string | null }
  >({
    mutationFn: (body) =>
      apiClient
        .patch<AgentRunDetail>(`/agent/runs/${runId}/configuration`, body)
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agent.run(data.run.submission_id), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.agent.all });
    },
  });
}

export function useCancelAgentRun(runId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<{ status: string }, Error, void>({
    mutationFn: () =>
      apiClient.post(`/agent/runs/${runId}/cancel`).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.agent.all });
    },
  });
}

export function useReplyAgentCheckpoint(runId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<
    { status: string; verdict: string },
    Error,
    { verdict: 'consistent' | 'mismatch'; note?: string }
  >({
    mutationFn: (body) =>
      apiClient.post(`/agent/runs/${runId}/reply`, body).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.agent.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.all });
    },
  });
}

const TERMINAL_RUN: AgentRunStatus[] = ['completed', 'failed', 'cancelled'];

/**
 * SSE 订阅 run 事件流;返回最近事件(至多 keep 100 条)。
 * EventSource 自带断线重连,页面刷新后由服务端回放历史事件恢复。
 */
export function useAgentRunEvents(
  runId: number | undefined,
  enabled: boolean,
  submissionId?: number,
) {
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (runId === undefined || !enabled) return;
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') return;
    const es = new EventSource(`/api/agent/runs/${runId}/stream`);
    es.addEventListener('run_event', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as AgentRunEvent;
        setEvents((prev) => {
          if (prev.length > 0 && prev[prev.length - 1].seq >= data.seq) return prev;
          const next = [...prev, data];
          return next.length > 100 ? next.slice(next.length - 100) : next;
        });
        // 状态可能有变:轻量刷新 run 详情
        queryClient.invalidateQueries({
          queryKey:
            submissionId !== undefined
              ? queryKeys.agent.run(submissionId)
              : queryKeys.agent.all,
        });
      } catch {
        // 忽略非法事件
      }
    });
    es.onerror = null;
    return () => {
      es.close();
    };
  }, [runId, enabled, submissionId, queryClient]);

  return events;
}

export function isTerminalAgentRun(status: AgentRunStatus): boolean {
  return TERMINAL_RUN.includes(status);
}
