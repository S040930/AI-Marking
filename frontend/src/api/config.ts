import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import { queryKeys } from './queryKeys';

// 配置项类型
export interface ConfigOut {
  paddleocr_api_url: string;
  paddleocr_token: string;
  rubric_definition: {
    items: { criterion: string; max_score: number; details: string }[];
    total_max_score: number;
  } | null;
  review_enabled: boolean;
}

export type ConfigUpdate = Partial<Omit<ConfigOut, 'rubric_definition'>> & {
  rubric_definition?: ConfigOut['rubric_definition'] | null;
};

// 配置项目类型
export interface ConfigProfile {
  id: number;
  name: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

// hooks
export function useConfig(profileId?: number) {
  return useQuery<ConfigOut>({
    queryKey: queryKeys.config.profile(profileId),
    queryFn: () =>
      apiClient
        .get<ConfigOut>('/config', {
          params: profileId ? { profile_id: profileId } : {},
        })
        .then((r) => r.data),
  });
}

export function useUpdateConfig(profileId?: number) {
  const queryClient = useQueryClient();
  return useMutation<ConfigOut, Error, ConfigUpdate>({
    mutationFn: (payload: ConfigUpdate) =>
      apiClient
        .put<ConfigOut>('/config', payload, {
          params: profileId ? { profile_id: profileId } : {},
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      // 直接写入缓存并 invalidate,确保 UI 立即同步
      queryClient.setQueryData(['config', profileId ?? 'default'], data);
      queryClient.invalidateQueries({ queryKey: queryKeys.config.all });
    },
  });
}

// 配置项目 CRUD hooks
export function useConfigProfiles() {
  return useQuery<ConfigProfile[]>({
    queryKey: queryKeys.config.profiles,
    queryFn: () =>
      apiClient
        .get<ConfigProfile[]>('/config/profiles')
        .then((r) => r.data),
  });
}

export function useCreateConfigProfile() {
  const queryClient = useQueryClient();
  return useMutation<
    ConfigProfile,
    Error,
    { name: string; copy_from_id?: number }
  >({
    mutationFn: (payload) =>
      apiClient
        .post<ConfigProfile>('/config/profiles', payload)
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.config.profiles });
    },
  });
}

export function useRenameConfigProfile() {
  const queryClient = useQueryClient();
  return useMutation<ConfigProfile, Error, { id: number; name: string }>({
    mutationFn: ({ id, name }) =>
      apiClient
        .patch<ConfigProfile>(`/config/profiles/${id}`, { name })
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.config.profiles });
    },
  });
}

export function useSetDefaultConfigProfile() {
  const queryClient = useQueryClient();
  return useMutation<ConfigProfile, Error, number>({
    mutationFn: (id) =>
      apiClient
        .post<ConfigProfile>(`/config/profiles/${id}/default`)
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.config.profiles });
    },
  });
}

export function useDeleteConfigProfile() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: (id) =>
      apiClient.delete(`/config/profiles/${id}`).then(() => undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.config.profiles });
    },
  });
}
