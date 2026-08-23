/** 批次 1：退款状态和客服审批前端契约。 */

import { describe, expect, it } from 'vitest'

import { ORDER_STATUS_MAP } from '@/api/order'
import router from '@/router'


describe('退款前端契约', () => {
  it('展示待退款审批状态', () => {
    expect(ORDER_STATUS_MAP.refund_requested).toBe('待退款审批')
  })

  it('退款审批路由允许 admin 和 agent', () => {
    const route = router.getRoutes().find((item) => item.name === 'order-refunds')
    expect(route?.meta.requiresRole).toEqual(['admin', 'agent'])
  })
})
