# PayPilot AI: Four-Problem Comparison

Research date: 2026-10-18

**Status:** Preliminary desk research. This is a prioritization hypothesis,
not proof of customer demand. Customer interviews and measured outcomes have
not yet been completed.

## 1. Confusing or incomplete payment evidence

**Question:** When a seller sees an unclear PayPal order or payment state,
can they determine what is known, what remains unknown, and what to check next?

**Initial opportunity:** Assemble available evidence, classify it using
deterministic rules, and explain the next safe investigation step.

**Risks and unknowns:** The frequency and cost of this problem are unknown.
An order record alone may not prove settlement, refund, or reconciliation.

## 2. Failed or declined payments

**Question:** Do sellers understand why a payment attempt failed, and can
they identify a useful next step from the evidence available to them?

**Initial opportunity:** Explain available decline information without
inventing a cause or promising that a retry will succeed.

**Risks and unknowns:** PayPal provides an Attempts and Declines report.
We must determine whether the target user already has sufficient information,
and whether the necessary data is accessible to our application.

## 3. Refunds, disputes, and unexpected changes

**Question:** Can a seller explain an unexpected balance or transaction change
using the records currently available?

**Initial opportunity:** Connect a specific change to supporting evidence
and identify missing or conflicting information.

**Risks and unknowns:** PayPal already offers case and balance reporting.
We must identify one concrete unresolved workflow rather than duplicate
existing reports.

## 4. Matching payments to invoices or records

**Question:** Where do payment references or amounts fail to match an
invoice or accounting record, and how are those exceptions resolved today?

**Initial opportunity:** Investigate a narrow class of ambiguous or
unresolved matches that existing tools do not handle clearly.

**Risks and unknowns:** PayPal reporting and accounting products already
support reconciliation workflows. Fincline advertises payment matching,
AI explanations, and human approval. A distinctive gap is not yet proven.

## Initial research priority

1. Confusing or incomplete payment evidence.
2. Failed or declined payment attempts.
3. Refunds, disputes, and unexpected changes.
4. Matching payments to invoices or records.

This order is provisional. It reflects fit with our existing read-only
prototype, not a proven ranking of customer pain or market size.

## Questions to investigate across all four problems

- Who experiences the problem, and in what business context?
- How often does it happen?
- What does the person do today, and how long does it take?
- Which evidence is difficult to find or interpret?
- What do PayPal and competing products already provide?
- Which facts can our application verify from accessible sources?
- How should the application handle missing or conflicting evidence?
- How can we measure correctness and useful time savings?

## Research rules

- Official documentation establishes documented capabilities, not customer
  demand or product effectiveness.
- Vendor pages describe vendor claims; they are not independent evaluations.
- Do not claim customer validation, time savings, accuracy improvements,
  or uniqueness without supporting evidence.
- Keep financial classification deterministic and explanations grounded.
- Treat missing or conflicting evidence as unresolved.
- Keep the initial workflow read-only; do not add money-moving operations.

## Sources to inspect

- PayPal Orders API: https://developer.paypal.com/docs/api/orders/v2/
- PayPal reports overview: https://developer.paypal.com/docs/reports/online-reports/
- PayPal reporting: https://developer.paypal.com/reports/
- QuickBooks: https://quickbooks.intuit.com/
- Fincline: https://fincline.ai/

For each source, record the exact page, the claim it supports, access date,
and any limitation. The QuickBooks homepage is a discovery starting point,
not sufficient evidence for a specific feature claim.

## Decision gate

Do not expand the product to all four workflows yet. First investigate the
target customer and a concrete scenario, compare the alternatives, and
verify the required data sources. Then choose one complete hackathon demo
workflow based on evidence.
