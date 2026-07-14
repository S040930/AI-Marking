import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

// 配置项类型
export interface ConfigOut {
  llm_api_key: string;
  llm_base_url: string;
  llm_model: string;
  review_llm_api_key: string;
  review_llm_base_url: string;
  review_llm_model: string;
  paddleocr_api_url: string;
  paddleocr_token: string;
  rubric: string;
  llm_user_prompt: string;
  operator_name: string;
}

export type ConfigUpdate = Partial<
  Pick<
    ConfigOut,
    | 'llm_api_key'
    | 'llm_base_url'
    | 'llm_model'
    | 'review_llm_api_key'
    | 'review_llm_base_url'
    | 'review_llm_model'
    | 'paddleocr_api_url'
    | 'paddleocr_token'
    | 'rubric'
    | 'llm_user_prompt'
    | 'operator_name'
  >
>;

// hooks
export function useConfig() {
  return useQuery<ConfigOut>({
    queryKey: ['config'],
    queryFn: () => apiClient.get<ConfigOut>('/config').then((r) => r.data),
  });
}

export function useUpdateConfig() {
  const queryClient = useQueryClient();
  return useMutation<ConfigOut, Error, ConfigUpdate>({
    mutationFn: (payload: ConfigUpdate) =>
      apiClient.put<ConfigOut>('/config', payload).then((r) => r.data),
    onSuccess: (data) => {
      // 直接写入缓存并 invalidate,确保 UI 立即同步
      queryClient.setQueryData(['config'], data);
      queryClient.invalidateQueries({ queryKey: ['config'] });
    },
  });
}
