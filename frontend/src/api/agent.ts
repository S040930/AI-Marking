import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, responseData } from './client';
import { queryKeys } from './queryKeys';
import type { components } from './generated';

// 批改 agent:run 生命周期与模型通道配置前端接口。

export type AgentRunStatus = components['schemas']['AgentRunOut']['status'];
export type AgentProviderName =
  components['schemas']['ProviderDefaultUpdateRequest']['provider'];

export type AgentCheckpoint = Omit<
  components['schemas']['AgentCheckpointOut'],
  'preview'
> & {
  preview?: { suite_id?: number; version?: number; tests?: CodeTestCase[] } | null;
};

export interface CodeTestCase {
  id?: string;
  question_number: number;
  title: string;
  mode: 'program_io';
  language: 'any' | 'python' | 'r' | 'java';
  input?: string;
  expected_stdout?: string;
}

export interface CodeTestResult extends CodeTestCase {
  id: string;
  status: 'passed' | 'failed' | 'compile_error' | 'runtime_error' | 'timeout' | 'missing_code' | 'not_run';
  output: string;
  /** `<backend>:<completeness>`，旧 run 没有该字段。 */
  isolation?: string;
  execution?: {
    elapsed_ms: number;
    peak_memory_bytes: number;
    peak_workdir_bytes: number;
    runtime_version: string;
    policy_version: string;
  };
}

export type AgentRun = Omit<
  components['schemas']['AgentRunOut'],
  'checkpoint' | 'code_test_suite' | 'code_test_results'
> & {
  checkpoint?: AgentCheckpoint | null;
  code_test_suite?: {
    suite_id?: number;
    id?: number;
    version: number;
    status?: string;
    tests: CodeTestCase[];
  } | null;
  code_test_results?: {
    suite_id: number;
    version: number;
    summary: { total: number; passed: number; failed: number; not_run?: number };
    results: CodeTestResult[];
  } | null;
};

export type AgentRunDetail = Omit<
  components['schemas']['AgentRunDetailResponse'],
  'run'
> & { run: AgentRun };

export type AgentRunEvent = Omit<
  components['schemas']['AgentRunEventOut'],
  'payload'
> & {
  payload: components['schemas']['AgentRunEventOut']['payload'] & {
    text?: string;
    title?: string;
    status?: string;
    /** 产出该事件的 agent:`supervisor` 或 `item:<rubric_item_id>` */
    agent?: string;
    [k: string]: unknown;
  };
};

export type AgentProviderStatus = components['schemas']['ProviderStatusOut'];
export type AgentProviderList = components['schemas']['ProviderListResponse'];
export type AgentOAuthStatus =
  components['schemas']['ProviderOAuthStatusResponse']['status'];
export type AgentOAuthStartResult =
  components['schemas']['ProviderOAuthStartResponse'];
export type AgentOAuthStatusResult =
  components['schemas']['ProviderOAuthStatusResponse'];
export type AgentProviderTestResult = components['schemas']['ProviderTestResponse'];
export type AgentProviderConfigUpdate =
  components['schemas']['ProviderConfigUpdateRequest'];

/** 模型通道列表(设置页与开始批改面板共用)。 */
export function useAgentProviders(
  enabled = true,
  modelsFor: AgentProviderName | null = null,
) {
  const providersQuery = useQuery<AgentProviderList>({
    queryKey: queryKeys.agent.providers,
    queryFn: () => responseData(apiClient.get<AgentProviderList>('/agent/providers')),
    retry: 1,
    enabled,
  });
  const modelsQuery = useQuery<AgentProviderTestResult>({
    queryKey: queryKeys.agent.providerModels(modelsFor ?? ''),
    queryFn: () =>
      responseData(
        apiClient.get<AgentProviderTestResult>(`/agent/providers/${modelsFor}/models`),
      ),
    enabled:
      enabled &&
      modelsFor !== null &&
      Boolean(providersQuery.data?.providers[modelsFor]?.available),
    staleTime: 60_000,
    retry: 0,
  });
  return {
    ...providersQuery,
    modelDiscovery: modelsQuery.data,
    modelsLoading: modelsQuery.isLoading,
  };
}

export function useSetDefaultProvider() {
  const queryClient = useQueryClient();
  return useMutation<AgentProviderList, Error, AgentProviderName>({
    mutationFn: (provider) =>
      responseData(apiClient.put<AgentProviderList>('/agent/providers/default', { provider })),
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
      responseData(apiClient.put<AgentProviderList>(`/agent/providers/${name}/config`, config)),
    onSuccess: (data, variables) => {
      queryClient.setQueryData(queryKeys.agent.providers, data);
      queryClient.invalidateQueries({
        queryKey: queryKeys.agent.providerModels(variables.name),
      });
    },
  });
}

/** 连接测试:有效 key 时同时返回可用模型列表。 */
export function useTestProvider() {
  return useMutation<AgentProviderTestResult, Error, AgentProviderName>({
    mutationFn: (name) =>
      responseData(apiClient.post<AgentProviderTestResult>(`/agent/providers/${name}/test`)),
  });
}

/** 发起 ChatGPT 订阅登录(PKCE);成功后把返回的地址交给浏览器打开。 */
export function useStartChatGptLogin() {
  const queryClient = useQueryClient();
  return useMutation<AgentOAuthStartResult, Error, void>({
    mutationFn: () =>
      responseData(apiClient.post<AgentOAuthStartResult>('/agent/providers/chatgpt_sub/oauth/start')),
    onSuccess: () => {
      // 清掉上一次登录留下的 completed 缓存,否则轮询会立即误报成功
      queryClient.setQueryData<AgentOAuthStatusResult>(
        queryKeys.agent.chatgptOauth,
        {
          status: 'pending',
          detail: null,
          authorize_url: null,
          expires_at: null,
        },
      );
    },
  });
}

/**
 * 轮询订阅登录结果;pending 时每 1.5s 拉一次,完成/失败后停止。
 * 完成时后端已把令牌落库,这里顺便刷新通道列表。
 */
export function useChatGptLoginStatus(enabled: boolean) {
  const queryClient = useQueryClient();
  const query = useQuery<AgentOAuthStatusResult>({
    queryKey: queryKeys.agent.chatgptOauth,
    queryFn: () =>
      responseData(apiClient.get<AgentOAuthStatusResult>('/agent/providers/chatgpt_sub/oauth/status')),
    enabled,
    retry: 0,
    refetchInterval: (q) =>
      q.state.data?.status === 'pending' ? 1500 : false,
  });
  const status = query.data?.status;
  useEffect(() => {
    if (status === 'completed' || status === 'failed') {
      queryClient.invalidateQueries({ queryKey: queryKeys.agent.providers });
      if (status === 'completed') {
        queryClient.invalidateQueries({
          queryKey: queryKeys.agent.providerModels('chatgpt_sub'),
        });
      }
    }
  }, [status, queryClient]);
  return query;
}

/** 退出 ChatGPT 订阅:清除令牌并把默认通道回落到官方 API。 */
export function useLogoutChatGpt() {
  const queryClient = useQueryClient();
  return useMutation<AgentProviderList, Error, void>({
    mutationFn: () =>
      responseData(apiClient.post<AgentProviderList>('/agent/providers/chatgpt_sub/oauth/logout')),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agent.providers, data);
      queryClient.setQueryData<AgentOAuthStatusResult>(
        queryKeys.agent.chatgptOauth,
        {
          status: 'idle',
          detail: null,
          authorize_url: null,
          expires_at: null,
        },
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.agent.all });
    },
  });
}

/** 查询某作业的活跃 run(404 视为无 run,不弹错误 toast)。 */
export function useActiveAgentRun(submissionId: number | undefined) {
  return useQuery<AgentRunDetail>({
    queryKey: queryKeys.agent.run(submissionId),
    queryFn: () =>
      responseData(apiClient.get<AgentRunDetail>(`/agent/runs/by-submission/${submissionId}`, {
          // 404 是「无活跃 run」的正常信号,轮询中反复弹错误 toast 会误导教师
          skipErrorToast: true,
        })),
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
  regenerate_code_tests?: boolean;
}

export function useCreateAgentRun(submissionId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<AgentRunDetail, Error, CreateAgentRunInput>({
    mutationFn: (body) =>
      responseData(apiClient.post<AgentRunDetail>('/agent/runs', {
          submission_id: submissionId,
          ...body,
        })),
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
      responseData(apiClient.patch<AgentRunDetail>(`/agent/runs/${runId}/configuration`, body)),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.agent.run(data.run.submission_id), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.agent.all });
    },
  });
}

export function useCancelAgentRun(runId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<{ status: string }, Error, void>({
    mutationFn: () => responseData(apiClient.post(`/agent/runs/${runId}/cancel`)),
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
    | { verdict: 'consistent' | 'mismatch'; note?: string }
    | { action: 'approve_test_suite' | 'regenerate_test_suite'; note?: string }
  >({
    mutationFn: (body) => responseData(apiClient.post(`/agent/runs/${runId}/reply`, body)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.agent.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.all });
    },
  });
}

const TERMINAL_RUN: AgentRunStatus[] = ['completed', 'failed', 'cancelled'];

/** 转录在内存中保留的最大事件数(超出丢弃最早的)。 */
export const MAX_AGENT_EVENTS = 300;

/**
 * SSE 订阅 run 事件流;返回最近事件(至多 {@link MAX_AGENT_EVENTS} 条)。
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
          return next.length > MAX_AGENT_EVENTS
            ? next.slice(next.length - MAX_AGENT_EVENTS)
            : next;
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
