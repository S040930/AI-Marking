import { z } from 'zod';

export const DEFAULT_RUBRIC_PLACEHOLDER = `请输入 JSON 结构，例如:
{
  "items": [
    {"criterion": "内容理解", "max_score": 60, "details": "准确理解题目要求"},
    {"criterion": "论证分析", "max_score": 40, "details": "论证清晰且有证据"}
  ],
  "total_max_score": 100
}`;
export const EN_RUBRIC_PLACEHOLDER = `Enter JSON, for example:
{
  "items": [
    {"criterion": "Content understanding", "max_score": 60, "details": "Understands the question accurately"},
    {"criterion": "Argument analysis", "max_score": 40, "details": "Clear argument supported by evidence"}
  ],
  "total_max_score": 100
}`;

export const configSchema = z.object({
  paddleocr_api_url: z.string().optional(),
  paddleocr_token: z.string().optional(),
  rubric_definition: z.string().optional(),
  review_enabled: z.boolean(),
});

export type ConfigFormValues = z.infer<typeof configSchema>;

export const defaultConfigFormValues: ConfigFormValues = {
  paddleocr_api_url: '',
  paddleocr_token: '',
  rubric_definition: '',
  review_enabled: true,
};

export function errorText(error: Error): string {
  const e = error as { response?: { data?: { detail?: string } } };
  return e.response?.data?.detail ?? error.message ?? '操作失败';
}

export interface ProfileDialogState {
  open: boolean;
  mode: 'create' | 'rename' | 'copy';
  profileId?: number;
  profileName?: string;
}

export const CLOSED_PROFILE_DIALOG: ProfileDialogState = {
  open: false,
  mode: 'create',
};
