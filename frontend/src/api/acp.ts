import { useEffect, useState } from 'react';
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { apiClient } from './client';
import { queryKeys } from './queryKeys';

// ACP 批改:agent 目录与 run 生命周期前端接口。

export type AcpConnectionStatus = 'ready' | 'needs_auth' | 'unsupported' | 'failed' | 'unknown';

export type CodexSelection = {
  modelId: string | null;
  reasoningEffort: string | null;
  speedMode: 'standard' | 'fast';
};

export interface AcpConfigSnapshot {
  model_id: string | null;
  reasoning_effort: string | null;
  speed_mode: 'standard' | 'fast';
  option_ids?: Record<string, string>;
  catalog_version?: string | null;
}

export interface CodexConfigurationCatalog {
  agent_id: 'codex-acp';
  agent_version: string;
  selected_model_id: string | null;
  models: { id: string; label: string; current: boolean }[];
  reasoning_efforts: { id: string; label: string; current: boolean }[];
  speed_modes: {
    id: 'standard' | 'fast';
    label: string;
    available: boolean;
    current: boolean;
    requires_confirmation?: boolean;
  }[];
  capability_error: string | null;
}

export type CodexConfigRequest = {
  model_id?: string | null;
  reasoning_effort?: string | null;
  speed_mode?: 'standard' | 'fast';
  fast_confirmed?: boolean;
};

export type AcpPermissionMode = 'ask' | 'auto_review';

export interface AcpAgent {
  agent_id: string;
  display_name: string;
  whitelist_package: string;
  distributions: string[];
  installed_version: string | null;
  available_version: string | null;
  connection_status: AcpConnectionStatus;
}

export interface AcpAgentsResponse {
  agents: AcpAgent[];
  registry_version: string | null;
  fetched_at: string | null;
}

export type AcpRunStatus =
  | 'queued'
  | 'starting'
  | 'running'
  | 'waiting_for_teacher'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface AcpCheckpoint {
  type: string;
  message: string;
  asked_at: string | null;
  expires_at: string | null;
}

export interface AcpRun {
  id: number;
  submission_id: number;
  agent_id: string;
  permission_mode: AcpPermissionMode;
  codex_config: AcpConfigSnapshot;
  applied_codex_config: AcpConfigSnapshot | null;
  pending_codex_config: AcpConfigSnapshot | null;
  effective_at: 'current' | 'next_turn';
  status: AcpRunStatus;
  acp_session_id: string | null;
  checkpoint: AcpCheckpoint | null;
  teacher_verdict: 'consistent' | 'mismatch' | null;
  teacher_note: string | null;
  error_message: string | null;
  attempts: number;
  max_attempts: number;
  created_at: string;
  finished_at: string | null;
}

export interface AcpRunDetail {
  run: AcpRun;
  latest_seq: number;
}

export interface AcpRunEvent {
  seq: number;
  kind: string;
  payload: { text?: string; title?: string; status?: string; [k: string]: unknown };
  created_at: string;
}

/** Codex 模型/思考强度/速度目录(对话面板与批改运行共用同一查询缓存)。 */
export function useCodexConfiguration(modelId?: string | null, enabled = true) {
  return useQuery<CodexConfigurationCatalog>({
    queryKey: queryKeys.acp.codexConfiguration(modelId),
    queryFn: () =>
      apiClient
        .get<CodexConfigurationCatalog>('/acp/codex/configuration', {
          params: modelId ? { model_id: modelId } : undefined,
        })
        .then((r) => r.data),
    // 切模型后 key 变化:保留上一份目录,避免下拉瞬间消失再回来
    placeholderData: keepPreviousData,
    retry: 1,
    enabled,
  });
}

export function useAcpAgents() {
  return useQuery<AcpAgentsResponse>({
    queryKey: queryKeys.acp.agents,
    queryFn: () => apiClient.get<AcpAgentsResponse>('/acp/agents').then((r) => r.data),
  });
}

export function useRefreshAcpRegistry() {
  const queryClient = useQueryClient();
  return useMutation<{ refreshed_at: number }, Error, void>({
    mutationFn: () => apiClient.post('/acp/agents/refresh').then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.agents });
    },
  });
}

export function useTestAcpAgent() {
  return useMutation<{ status: string; detail?: string; agent_info?: string }, Error, string>({
    mutationFn: (agentId) =>
      apiClient
        .post<{ status: string; detail?: string; agent_info?: string }>(
          `/acp/agents/${agentId}/test`,
        )
        .then((r) => r.data),
  });
}

export function useInstallAcpAgent() {
  const queryClient = useQueryClient();
  return useMutation<
    { agent_id: string; version: string; distribution: string; reinstalled: boolean },
    Error,
    string
  >({
    mutationFn: (agentId) =>
      apiClient
        .post<{
          agent_id: string;
          version: string;
          distribution: string;
          reinstalled: boolean;
        }>(`/acp/agents/${agentId}/install`)
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.all });
    },
  });
}

/** 卸载已安装链接:agent 仍留在白名单中,状态回到未安装。 */
export function useUninstallAcpAgent() {
  const queryClient = useQueryClient();
  return useMutation<
    { agent_id: string; version: string | null; uninstalled: boolean; removed_cache: boolean },
    Error,
    string
  >({
    mutationFn: (agentId) =>
      apiClient
        .delete<{
          agent_id: string;
          version: string | null;
          uninstalled: boolean;
          removed_cache: boolean;
        }>(`/acp/agents/${agentId}/install`)
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.all });
    },
  });
}

export function useAcpDefaultAgent() {
  return useQuery<{ agent_id: string }>({
    queryKey: queryKeys.acp.defaultAgent,
    queryFn: () =>
      apiClient.get<{ agent_id: string }>('/acp/default-agent').then((r) => r.data),
  });
}

export function useSetDefaultAcpAgent() {
  const queryClient = useQueryClient();
  return useMutation<{ default_agent: string }, Error, string>({
    mutationFn: (agentId) =>
      apiClient
        .post<{ default_agent: string }>(`/acp/agents/${agentId}/set-default`)
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.all });
    },
  });
}

/** 查询某作业的活跃 run(404 视为无 run)。 */
export function useActiveAcpRun(submissionId: number | undefined) {
  return useQuery<AcpRunDetail>({
    queryKey: queryKeys.acp.run(submissionId),
    queryFn: () =>
      apiClient
        .get<AcpRunDetail>(`/acp/runs/by-submission/${submissionId}`, {
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

export function useCreateAcpRun(submissionId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<
    AcpRunDetail,
    Error,
    { permission_mode: AcpPermissionMode; codex_config: CodexConfigRequest }
  >({
    mutationFn: (body) =>
      apiClient
        .post<AcpRunDetail>('/acp/runs', {
          submission_id: submissionId,
          ...body,
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.acp.run(data.run.submission_id), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.submissions.all });
    },
  });
}

/** 下拉选择 → 后端请求体;选 fast 必须带确认标记,否则后端 422。 */
export function selectionToRequest(selection: CodexSelection): CodexConfigRequest {
  return {
    model_id: selection.modelId,
    reasoning_effort: selection.reasoningEffort,
    speed_mode: selection.speedMode,
    fast_confirmed: selection.speedMode === 'fast',
  };
}

/**
 * 运行中热切换模型/思考强度/速度。
 * 后端按 run 当前状态决定生效时机:未启动的 run 立即生效,进行中的 run
 * 排队到下一回合(响应里 effective_at === 'next_turn')。
 */
export function useUpdateRunCodexConfiguration(runId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<AcpRunDetail, Error, CodexConfigRequest>({
    mutationFn: (body) =>
      apiClient
        .patch<AcpRunDetail>(`/acp/runs/${runId}/codex-configuration`, body)
        .then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.acp.run(data.run.submission_id), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.all });
    },
  });
}

export function useCancelAcpRun(runId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<{ status: string }, Error, void>({
    mutationFn: () => apiClient.post(`/acp/runs/${runId}/cancel`).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.acp.all });
    },
  });
}

export function useReplyAcpCheckpoint(runId: number | undefined) {
  const queryClient = useQueryClient();
  return useMutation<{ status: string; verdict: string }, Error, { verdict: 'consistent' | 'mismatch'; note?: string }>(
    {
      mutationFn: (body) =>
        apiClient.post(`/acp/runs/${runId}/reply`, body).then((r) => r.data),
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: queryKeys.acp.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.submissions.all });
      },
    },
  );
}

const TERMINAL_RUN: AcpRunStatus[] = ['completed', 'failed', 'cancelled'];

/**
 * SSE 订阅 run 事件流;返回最近事件(至多 keep 100 条)。
 * EventSource 自带断线重连,页面刷新后由服务端回放历史事件恢复。
 * 事件高频到达:传入 submissionId 时失效范围收窄到该 submission 的
 * run 查询,避免每次事件都整棵 ['acp'] 前缀(agent 探测/Codex 配置
 * 握手等)跟着空转。
 */
export function useAcpRunEvents(
  runId: number | undefined,
  enabled: boolean,
  submissionId?: number,
) {
  const [events, setEvents] = useState<AcpRunEvent[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (runId === undefined || !enabled) return;
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') return;
    const es = new EventSource(`/api/acp/runs/${runId}/stream`);
    es.addEventListener('run_event', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as AcpRunEvent;
        setEvents((prev) => {
          if (prev.length > 0 && prev[prev.length - 1].seq >= data.seq) return prev;
          const next = [...prev, data];
          return next.length > 100 ? next.slice(next.length - 100) : next;
        });
        // 状态可能有变:轻量刷新 run 详情
        queryClient.invalidateQueries({
          queryKey:
            submissionId !== undefined
              ? queryKeys.acp.run(submissionId)
              : queryKeys.acp.all,
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

export function isTerminalRun(status: AcpRunStatus): boolean {
  return TERMINAL_RUN.includes(status);
}
