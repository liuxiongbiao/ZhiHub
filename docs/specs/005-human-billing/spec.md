# Feature Specification: Human-in-the-loop + 计费闸门

**Feature Branch**: `005-human-billing` | **Created**: 2026-08-04 | **Status**: Draft

## User Scenarios

### US1 — 退款转人工 + 坐席认领 (P1) 🎯
用户申请退款→强制转人工→坐席认领（互斥锁）→坐席回复。两个坐席同时认领同一会话只有一个成功。

### US2 — 配额计数 + 预警 (P1) 🎯
每次Agent完成有效回复→配额+1。达到80%触发预警通知，达到100%超额标记（不拒绝服务）。

### US3 — 双维度限流 (P2)
租户级别+用户级别令牌桶限流。租户100次/分钟，单用户20次/分钟。

### US4 — 账单 + 用量看板 (P2)
月度账单生成（基础费+超额费）、用量概览、满意度评价收集。

## Requirements (摘要)
- FR-001: 转人工触发条件(angry/sensitive/low_confidence)汇总到human_handoff节点
- FR-002: 坐席认领用Redis SETNX原子锁，双坐席同时认领只有一个成功
- FR-003: 配额计数会话RESOLVED时INCR，80%/100%阈值触发预警
- FR-004: 令牌桶限流双维度（租户+用户），超限返回429
- FR-005: 月度账单+用量看板+满意度评价

## Success Criteria
- SC-001: 双坐席同时认领同一会话，仅1人成功
- SC-002: 会话达配额80%触发预警通知
- SC-003: 限流阈值内正常通过，超限3次内返回429
