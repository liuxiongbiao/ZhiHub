# Implementation Plan: MCP Server + 工单/订单Agent

**Branch**: `004-mcp-ticket-order` | **Date**: 2026-08-04 | **Spec**: [spec.md](./spec.md)

## Summary
实现 MCP 真实协议（JSON-RPC 2.0）的工单/订单工具集 + 订单状态机（乐观锁+退款上限）+ 工单/订单Agent节点 + 幂等基础设施。

## Technical Context
**Language/Version**: Python 3.14 + Vue 3
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0, Redis, LangGraph
**Storage**: MySQL (工单/订单/退款), Redis (幂等键 24h TTL)
**Testing**: pytest
**Project Type**: Web application

## Constitution Check
| Principle | Status |
|-----------|--------|
| I. Layered Boundaries | ✅ API → Service → CRUD |
| II. Tenant Isolation | ✅ tenant_id on all queries |
| III. Money & State Safety | ✅ 状态机+乐观锁+退款上限+幂等 |
| IV. E2E Acceptance | ✅ 3验收场景 |
| V. Depth Over Breadth | ✅ MCP是核心链路 |

## Project Structure
See spec.md for file inventory. All 18 files implemented.
