/** 工单、退款、会话和审计列表原来共用的本地时间格式。空值可返回指定占位。 */
export function formatDateTime(iso: string, emptyLabel?: string): string {
  if (!iso && emptyLabel !== undefined) return emptyLabel
  return new Date(iso).toLocaleString('zh-CN')
}
