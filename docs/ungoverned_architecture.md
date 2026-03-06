# Ungoverned Architecture

## Overview
Ungoverned is a Django-based internal operations app for managing orders, builds, inventory consumption, stock returns, and shipping for a small manufacturing workflow.

The app currently supports a practical order lifecycle:

**Pending -> Building -> Completed -> Shipped**

with controlled reversal paths:

- **Cancel Order**
- **Cancel Build**
- **Reopen Order**

A key design principle is that inventory changes are tracked through an append-only style ledger model (`StockMovement`) rather than by changing stock silently.

---

## Core Workflow

### 1. Order creation
Orders are created and associated with a customer.

Relevant data includes:
- customer
- order date
- status
- shipping information
- cancellation information
- internal notes

Typical initial status:
- `pending`

### 2. Start Build
From the Orders page, a pending order can be moved into production.

Flow:
1. User clicks **Start Build**
2. Order is routed into the build flow / BOM page
3. A `ProductBuild` record is created
4. Component inventory is consumed
5. `StockMovement` entries are created with reason `BUILD_CONSUME`
6. Order status changes from `pending` to `building`

### 3. Build management
While an order is in `building`:
- it can be marked complete
- it can be cancelled

When building begins, build timing information becomes visible on the order detail page.

### 4. Mark Complete
Once production is finished:
- order status changes from `building` to `completed`

This indicates the build is done and the order is ready for shipping.

### 5. Ship Order
Completed orders can be shipped.

Typical fields involved:
- shipping date
- shipping tracking number

Order status changes from `completed` to `shipped`.

### 6. Cancel Build
A build can be cancelled safely.

Flow:
1. Locate the linked `ProductBuild`
2. For each `ProductComponent` in the built product:
   - calculate quantity to return
   - create a `StockMovement` entry with reason `BUILD_CANCEL_RETURN`
3. If the linked order is `building`, revert it to `pending`
4. Delete the `ProductBuild`

Purpose:
- safely reverse component consumption
- return stock to inventory
- preserve a movement history

### 7. Cancel Order
An order can be cancelled when in `pending` or `building`.

Flow:
1. User opens a confirmation page
2. Optional cancellation reason is entered
3. Order is locked in a transaction
4. If a build exists:
   - consumed stock is returned via `BUILD_CANCEL_RETURN`
   - build record is removed
5. Order status changes to `cancelled`
6. `cancelled_at` timestamp is set
7. `cancellation_reason` is stored
8. An audit line can be appended to notes

### 8. Reopen Order
Cancelled orders can be reopened and returned to the pending pool.

Flow:
1. Order must currently be `cancelled`
2. There must be no active `ProductBuild` linked to the order
3. Order status changes back to `pending`
4. Cancellation history is retained
5. An audit line is appended to `notes`

This supports real-world scenarios such as:
- customer payment issues resolved later
- priorities changing mid-production
- production being paused and resumed later

---

## Main Models

## Order
Represents the business-side lifecycle of a customer order.

Important concepts currently in use:
- `status`
  - `pending`
  - `building`
  - `completed`
  - `shipped`
  - `cancelled`
- `order_date`
- `shipping_date`
- `shipping_tracking_number`
- `cancelled_at`
- `cancellation_reason`
- `notes`
- relation to `Customer`
- relation to `OrderItem`
- relation to `ProductBuild`

Responsibilities:
- workflow state management
- cancellation and reopen handling
- shipping info
- internal notes / audit trail

## ProductBuild
Represents a build event for a product, optionally linked to an order.

Known fields in use:
- `product`
- `order`
- `quantity`
- `built_at`

Responsibilities:
- record that a product build occurred
- link production activity to an order
- act as a reference for inventory consumption / reversal

## ProductComponent
Maps a product to its required inventory components.

Known usage:
- used to determine what stock must be consumed during build
- used to determine what stock must be returned during build cancellation

Typical purpose:
- bill of materials logic
- per-product component requirements

## StockMovement
Inventory ledger model.

This is the core audit model for inventory.

Known movement reasons in use include:
- `BUILD_CONSUME`
- `BUILD_CANCEL_RETURN`

Responsibilities:
- record every stock change as a movement
- support historical traceability
- avoid silent stock mutations without explanation

Design note:
Stock levels should be explainable by summing movements or by using movements to justify current quantity changes.

## OrderItem
Represents line items belonging to an order.

Responsibilities:
- define what products / quantities are included in the order
- support order detail display
- provide future support for richer build and fulfillment logic

---

## Main Pages / Views

## Orders List
Primary operations dashboard.

Current behaviour:
- shows all orders
- supports status filtering
- custom status ordering for display
- includes:
  - Order ID
  - customer
  - country
  - order date
  - status
  - info link
  - shipping info
  - actions

Current actions by status:
- `pending`
  - Start Build
  - Cancel
- `building`
  - Mark Complete
  - Cancel
- `completed`
  - Ship Order
- `cancelled`
  - Reopen
- `shipped`
  - no state-changing action currently

## Order Detail
Information hub for a single order.

Currently intended to show:
- order info
- build information
- order items
- cancellation information (if present)
- internal notes

This page is now the preferred place for information display, while the Orders page remains workflow-focused.

## Cancel Order Page
Confirmation page before final cancellation.

Shows:
- customer / order details
- whether a build exists
- warning text about stock return and cancellation
- optional cancellation reason form

## Vendetta BOM / Build Page
Build-oriented page used during production flow.

Current known role:
- receives order context
- helps perform build-related actions
- participates in creating `ProductBuild` and inventory consumption records

---

## Status Behaviour Summary

### Pending
- can start build
- can cancel order

### Building
- indicates production in progress
- can mark complete
- can cancel order
- can cancel build and return stock

### Completed
- build done
- can ship order

### Shipped
- shipped to customer
- tracking may be present
- no further workflow action currently

### Cancelled
- order removed from active workflow
- cancellation metadata stored
- can be reopened to `pending`

---

## Inventory Design Notes

### Ledger-first approach
A major strength of the current design is use of a stock movement ledger.

Instead of only adjusting component quantities directly, the system records why stock changed.

Benefits:
- easier debugging
- safer reversals
- auditability
- clearer future reporting

### Build consume / return symmetry
The build and cancel-build logic is intentionally symmetric:
- build consumes components
- cancel build returns components

This reduces risk of inventory drift.

---

## Audit / Traceability

Currently auditability is provided by two mechanisms:

### 1. StockMovement
Tracks inventory-side events.

### 2. Order notes
Tracks business/process-side events.

Examples of useful notes entries:
- cancelled by user with reason
- reopened by user
- payment issue noted
- customer requested hold

Future enhancement:
A dedicated `OrderEvent` model could eventually replace or complement free-text notes.

---

## Current Strengths

The app already has several strong architecture decisions for a prototype:

- clear order lifecycle
- reversible production actions
- inventory ledger rather than silent stock mutation
- transaction use for critical workflows
- order-level audit notes
- separation between info pages and action buttons

---

## Recommended Next Improvements

### Near-term
- component ledger page
- richer build information on order detail
- show movement history tied to order/build
- search on orders page
- performance tuning with `select_related` / `prefetch_related`

### Medium-term
- dedicated `OrderEvent` audit model
- better build-to-order item mapping
- shipping workflow refinement
- reporting views for stock usage and build history

---

## Component Ledger Page Idea
A useful next feature is:

**Click a component -> see full movement history**

Suggested contents:
- current stock
- all `StockMovement` rows for that component
- timestamp
- reason
- quantity delta
- running balance (optional)
- related reference/build/order if available

This would greatly improve inventory debugging and trust in stock figures.
