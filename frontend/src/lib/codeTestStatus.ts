import type { CodeTestResult } from '@/api/agent';

export const CODE_TEST_STATUS_LABEL: Record<CodeTestResult['status'], string> = {
  passed: '通过',
  failed: '未通过',
  compile_error: '编译错误',
  runtime_error: '运行异常',
  timeout: '超时',
  missing_code: '缺少代码',
  not_run: '未自动执行',
};

export function testStatusLabel(status: CodeTestResult['status']): string {
  return CODE_TEST_STATUS_LABEL[status] ?? status;
}

/** `backend:completeness` 的人类可读文案；未知取值原样返回，缺失返回 null。 */
export function isolationLabel(value: string | undefined): string | null {
  if (!value) return null;
  const known: Record<string, string> = {
    'container:full': '容器隔离（完整）',
    'seatbelt:partial': '沙箱隔离（受限）',
    'seatbelt:full': 'macOS 沙箱隔离',
    none: '无隔离',
  };
  return known[value] ?? value;
}

/**
 * 只在隔离被降级时提示。
 * `*:full` 表示完整隔离；旧 run 没有该字段，不应在界面制造噪音。
 */
export function degradedIsolation(value: string | undefined): boolean {
  return Boolean(value) && !value?.endsWith(':full');
}
