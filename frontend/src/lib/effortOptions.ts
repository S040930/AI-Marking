/**
 * 思考强度档位(Responses API reasoning.effort);空串表示用模型默认。
 *
 * 订阅端实测只认 low..max:目录声明的 ultra 与 minimal 都会被 400 拒绝
 * (见 docs/architecture/chatgpt-sub-spike.md §A6),因此按通道分开维护。
 */
export const OPENAI_EFFORT_OPTIONS = ['', 'minimal', 'low', 'medium', 'high'] as const;

export const SUBSCRIPTION_EFFORT_OPTIONS = [
  '',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
] as const;

export function effortOptionsFor(
  provider: string | null | undefined,
): readonly string[] {
  return provider === 'chatgpt_sub'
    ? SUBSCRIPTION_EFFORT_OPTIONS
    : OPENAI_EFFORT_OPTIONS;
}
