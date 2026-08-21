import axios from 'axios';

export function errorMessage(error: Error): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : detail?.message ?? error.message;
  }
  return error.message;
}
