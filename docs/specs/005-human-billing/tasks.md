# Tasks: Human-in-the-loop + 计费闸门

**Status**: ✅ All completed (166 tests pass)

## Phase 1: Models
- [x] T001 UsageRecord model
- [x] T002 BillingInvoice model
- [x] T003 CsatEvaluation model

## Phase 2: Core Services
- [x] T004 Human Service (SETNX claim lock + return + auto-ticket)
- [x] T005 Billing Service (INCR quota + 80%/100% warnings + invoice)
- [x] T006 Rate Limit Service (dual-dimension sliding window)

## Phase 3: Agent + Middleware
- [x] T007 Human Handoff Node (3 trigger conditions)
- [x] T008 Rate Limit Middleware

## Phase 4: API
- [x] T009 Billing API (quota/invoices/usage)
- [x] T010 Session claim/return endpoints

## Phase 5: Tests
- [x] T011 HITL trigger conditions
- [x] T012 Claim lock atomicity
- [x] T013 Rate limit thresholds
- [x] T014 Billing quota thresholds
