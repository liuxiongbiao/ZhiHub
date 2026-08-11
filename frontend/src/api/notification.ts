// frontend/src/api/notification.ts
// 站内信接口（closure-plan P1-5）
import request from './request'

export interface NotificationItem {
  id: number
  title: string
  content: string | null
  type: string
  read_at: string | null
  created_at: string
}

export function getNotifications(params: { unread?: boolean; page?: number; page_size?: number } = {}) {
  return request.get<{ items: NotificationItem[]; total: number; page: number; page_size: number }>('/notifications', { params })
}

export function getUnreadCount() {
  return request.get<{ count: number }>('/notifications/unread-count')
}

export function markNotificationRead(id: number) {
  return request.post<{ id: number; read: boolean }>(`/notifications/${id}/read`)
}
