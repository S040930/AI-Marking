import axios from 'axios';
import type { SubmissionStatus } from '@/api/submissions';

export const STATUS_TEXT: Record<SubmissionStatus, string> = {
  pending: '待处理',
  ocr_processing: 'OCR识别中',
  ocr_done: 'OCR完成',
  awaiting_mcp: '等待MCP评分',
  ready_for_review: '待审阅',
  reviewed: '已审阅',
  failed: '失败',
};

export function isDeletableStatus(status: SubmissionStatus): boolean {
  return (
    status === 'ready_for_review' ||
    status === 'awaiting_mcp' ||
    status === 'reviewed' ||
    status === 'failed'
  );
}

export function shouldMoveToPreviousPage(
  page: number,
  rowCount: number,
  deletedCount: number,
): boolean {
  return page > 1 && rowCount > 0 && rowCount <= deletedCount;
}

export function resolveDeleteError(error: unknown): {
  message: string;
  isConflict: boolean;
} {
  if (axios.isAxiosError(error) && error.response?.status === 409) {
    const detail = error.response.data?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return { message: String(detail.message), isConflict: true };
    }
    return {
      message: '正在处理的记录不可删除，请等待批改完成后重试',
      isConflict: true,
    };
  }
  return { message: '删除失败，请稍后重试', isConflict: false };
}
