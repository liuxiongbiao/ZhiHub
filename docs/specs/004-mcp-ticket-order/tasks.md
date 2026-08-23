# Tasks: MCP Server + 工单/订单Agent

**Status**: ✅ All completed (166 tests pass)

## Phase 1: Models
- [x] T001 Ticket model
- [x] T002 Order model (with optimistic lock version)
- [x] T003 RefundApproval model (idempotency key)

## Phase 2: Core Services
- [x] T004 MCP Service (permission matrix + param validation)
- [x] T005 Order Service (state machine + refund validation)
- [x] T006 Ticket Service (CRUD + status transitions)
- [x] T007 Idempotency core + service

## Phase 3: MCP Servers
- [x] T008 Ticket MCP Server (JSON-RPC 2.0 tools)
- [x] T009 Order MCP Server (JSON-RPC 2.0 tools)

## Phase 4: Agent Nodes
- [x] T010 Ticket Agent (slot filling + MCP call)
- [x] T011 Order Agent (slot filling + MCP call)

## Phase 5: API + Seed
- [x] T012 Ticket API
- [x] T013 Order API
- [x] T014 MCP API (tools/list + tools/call)
- [x] T015 Seed script

## Phase 6: Tests
- [x] T016 Order state machine tests
- [x] T017 MCP permission tests
- [x] T018 Ticket/Order agent tests
