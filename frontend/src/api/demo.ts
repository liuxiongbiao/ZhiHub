import request from './request'
import type { DemoPersona } from '@/types/auth'

export interface DemoScenario {
  id: 'knowledge' | 'order' | 'handoff'
  title: string
  prompt: string
  outcome: string
}

export interface DemoStatus {
  is_demo: boolean
  can_restore: boolean
  customer_phone_last4: string | null
  order_no: string | null
  widget_api_key: string | null
  scenarios: DemoScenario[]
}

export function getDemoStatus() {
  return request.get<DemoStatus>('/demo/status')
}

export function restoreDemo() {
  return request.post('/demo/restore', { confirm: 'RESTORE_DEMO' })
}

export const DEMO_ROLE_META: Record<DemoPersona, { label: string; description: string }> = {
  admin: { label: '管理员', description: '看全局、配知识、做审批' },
  agent1: { label: '一线坐席', description: '认领会话并处理售后' },
  agent2: { label: '协作坐席', description: '验证多人协同与抢单' },
}
