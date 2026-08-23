# Implementation Plan: Human-in-the-loop + 计费闸门

**Branch**: `005-human-billing` | **Date**: 2026-08-04 | **Spec**: [spec.md](./spec.md)

## Summary
实现 Human-in-the-loop（Redis SETNX 分布式认领锁）+ 配额计数（80%/100%预警）+ 双维度令牌桶限流 + 月度账单。

## Technical Context
**Language/Version**: Python 3.14 + Vue 3
**Primary Dependencies**: FastAPI, Redis, SQLAlchemy 2.0
**Storage**: MySQL (usage/invoice/csat), Redis (lock/quota/rate)
**Testing**: pytest
**Project Type**: Web application

## Constitution Check
| Principle | Status |
|-----------|--------|
| I. Layered Boundaries | ✅ API → Service → middleware |
| II. Tenant Isolation | ✅ tenant_id on all |
| III. Money & State Safety | ✅ billing 幂等+退款防线 |
| IV. E2E Acceptance | ✅ 双坐席认领+配额预警 |
| V. Depth Over Breadth | ✅ HITL是核心链路 |

## Project Structure
See spec.md for file inventory. All 14 files implemented.
