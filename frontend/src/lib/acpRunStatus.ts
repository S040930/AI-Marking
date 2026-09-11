/**
 * ACP 批改 run 的状态文案。
 *
 * 注意有三个**不同领域**的同名映射,不要合并:
 * - 作业状态:`lib/submissionStatus.ts` 的 STATUS_TEXT
 * - 对话状态:`AcpChatPanel` 内部的 STATUS_LABEL
 * - 批改 run:本文件的 RUN_STATUS_LABEL
 */
export const RUN_STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  starting: '启动 Codex 中',
  running: '批改进行中',
  cancelling: '取消中',
  waiting_for_teacher: '等待教师确认',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABEL[status] ?? status;
}
