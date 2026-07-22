import type { TaskPayloadSummary } from '@/types/mongars-api';

export function payloadSummaryPreview(summary: TaskPayloadSummary): string {
  if (summary.preview_omitted_characters === 0) return summary.preview_head;

  return `${summary.preview_head}\n\n… ${summary.preview_omitted_characters.toLocaleString()} characters omitted …\n\n${summary.preview_tail}`;
}

export function formatPayloadBytes(byteLength: number): string {
  if (byteLength < 1_024) return `${byteLength} B`;
  if (byteLength < 1_048_576) return `${(byteLength / 1_024).toFixed(1)} KiB`;
  return `${(byteLength / 1_048_576).toFixed(2)} MiB`;
}
