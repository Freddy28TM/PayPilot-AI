# Customer Problem Evidence — PayPilot AI

## Purpose
Record what available evidence supports, what remains unproven, and what to investigate next.

## 1. General small-business payment challenges
The Federal Reserve Bank of Boston reported in February 2025 that 80% of respondents to its Small Business Credit Survey reported challenges sending or receiving payments. This supports the broad importance of payment operations, but does not establish how often small businesses struggle to interpret PayPal order evidence.

Source: Federal Reserve Bank of Boston, report published February 13, 2025. See the research notes in docs/COMPETITION-RESEARCH.md for the source reference.

## 2. Existing PayPal reporting
PayPal documents reports covering payment activity, fees, payouts, declines, disputes, and reconciliation. Some reports have access, format, or integration prerequisites. PayPilot must offer value beyond simply displaying information that merchants can already obtain from PayPal.

Source: PayPal Developer documentation on reports and the Attempts and Declines report; see docs/COMPETITION-RESEARCH.md.

## 3. Existing competitor coverage
Fincline describes payment matching, AI explanations, human confirmation, and audit trails. BlackLine and Trintech offer reconciliation and exception-management capabilities. These establish that adjacent solutions exist; they do not prove that our proposed workflow is unique or that small businesses would adopt it.

Source: Competitor desk research recorded in docs/COMPETITION-RESEARCH.md. No hands-on product comparison has been completed.

## 4. Current hypothesis — not validated
When a small business owner encounters a confusing PayPal order or payment exception, PayPilot AI assembles available evidence, explains what it establishes, identifies missing information, and recommends the next safe investigation step.

## 5. What we still need to learn
- How often merchants encounter confusing or incomplete payment evidence.
- How much time these cases take and what errors or delays result.
- Which evidence merchants can access through supported PayPal APIs or reports.
- Whether existing PayPal tools and accounting products already solve the problem adequately.
- Whether users find the explanation accurate, useful, and safe.

## 6. Validation plan
1. Interview small-business sellers or people who reconcile payments; ask about their most recent real example.
2. Record frequency, current workaround, time spent, consequences, and evidence sources.
3. Compare all four problem areas in docs/FOUR-PROBLEM-COMPARISON.md.
4. Test synthetic cases for known, missing, conflicting, and ambiguous evidence.
5. Measure whether users can correctly explain the case and choose an appropriate next step after using the prototype.

## Research integrity
Do not describe the hypothesis as proven. Do not invent customer interviews, measured savings, accuracy rates, or market gaps. Distinguish sourced facts from assumptions and future tests.

## 7. Online source register

### Source A — Small-business payment challenges
Publisher: Federal Reserve Banks
Published: December 5, 2024
URL: https://www.fedsmallbusiness.org/reports/survey/2024/2024-report-on-payments
Finding: Roughly four in five surveyed small firms faced payment-related challenges.
Limit: Broad US survey finding; it does not prove demand for PayPilot AI or isolate confusing PayPal order states.

### Source B — PayPal Balance Reconciliation report
Publisher: PayPal Developer
URL: https://developer.paypal.com/reports/balance
Finding: Covers payment activity, disputes, fees, refunds, withdrawals, and balance reconciliation.
Limit: CSV report; availability and delivery have prerequisites. Confirm which sources are accessible to our intended users.

### Source C — PayPal transactions in QuickBooks
Publisher: Intuit
URL: https://quickbooks.intuit.com/
Finding: Supports importing, reviewing, categorizing, and matching PayPal transactions.
Limit: Product documentation establishes available features, not how well every user performs reconciliation.

### Current interpretation
The sources support the existence of payment-operation challenges and established reporting and reconciliation tools. They do not establish that confusing order evidence is the most frequent or costly problem, nor that AI is the best solution.

### Next validation
Test synthetic cases against deterministic expected results. Measure whether the explanation correctly distinguishes known facts, missing evidence, and uncertainty. Do not claim customer validation or measured time savings until actually tested.
