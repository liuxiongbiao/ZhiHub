import request from './request'

export interface DeepTaskStep {
  step: number
  label: string
  elapsed_ms?: number
  thought?: string
  action?: string
  action_input?: string
  observation?: string
}

export interface DeepTask {
  task_id: string
  goal: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'truncated'
  steps: DeepTaskStep[]
  report?: string
  error?: string
  total_elapsed_ms?: number
  token_consumed?: number
  token_budget?: number
  termination_reason?: string
  created_at?: string
}

export function createDeepTask(goal: string) {
  return request.post<DeepTask>('/agent/deep-task', { goal })
}

export function getDeepTask(taskId: string) {
  return request.get<DeepTask>(`/agent/deep-task/${taskId}`)
}

export function getDeepTasks(limit = 20) {
  return request.get<{ items: DeepTask[] }>('/agent/deep-tasks', { params: { limit } })
}
