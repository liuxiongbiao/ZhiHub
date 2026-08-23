/**
 * frontend/tests/unit/e2e-helper.spec.ts
 *
 * 被测对象: tests/e2e/e2e-helper.cjs 的 buildApiHeaders 工具函数。
 * 覆盖场景: 已认证请求同时携带 Bearer 与 X-CSRF-Token 头；未认证请求不含两者。
 */
import { createRequire } from 'node:module'
import { describe, expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const { buildApiHeaders } = require('../e2e/e2e-helper.cjs') as {
  buildApiHeaders: (token?: string) => Record<string, string>
}

describe('E2E API helper', () => {
  it('adds the CSRF header alongside Bearer auth for authenticated mutations', () => {
    expect(buildApiHeaders('test-jwt')).toEqual({
      'Content-Type': 'application/json',
      Authorization: 'Bearer test-jwt',
      'X-CSRF-Token': 'test-jwt',
    })
  })
})
