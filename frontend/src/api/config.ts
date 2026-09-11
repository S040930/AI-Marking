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

// hooks
export function useConfig() {
  return useQuery<ConfigOut>({
    queryKey: queryKeys.config.all,
    queryFn: () =>
      apiClient.get<ConfigOut>('/config').then((r) => r.data),
  });
}

export function useUpdateConfig() {
  const queryClient = useQueryClient();
  return useMutation<ConfigOut, Error, ConfigUpdate>({
    mutationFn: (payload: ConfigUpdate) =>
      apiClient.put<ConfigOut>('/config', payload).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.config.all, data);
      queryClient.invalidateQueries({ queryKey: queryKeys.config.all });
    },
  });
}
