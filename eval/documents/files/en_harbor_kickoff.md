# Harbor kickoff

Fictional company: Lumenfield Logistics. Date: 12 February 2026.

## Attendees

Priya Halvorsen (project lead), Tomasz Wren (backend), Odile Marchetti (finance systems).

## Context

Project Harbor replaces the nightly batch that copies shipment records from the Ledgerline billing
service into the Tidewater reporting warehouse. The batch runs for about four hours and fails
roughly once a week, which leaves the morning reports empty.

## Decisions

- We decided to stream shipment events from Ledgerline into Tidewater through a change-data-capture
  pipeline instead of the nightly batch. Priya Halvorsen proposed this approach.
- Reports in Tidewater must be at most fifteen minutes behind Ledgerline.

## Risks

Odile Marchetti warned that Ledgerline invoices are sometimes corrected after the monthly close, so
Tidewater has to accept late updates to records it already holds.

## Actions

- Tomasz Wren will draft the shipment event schema by 3 March 2026.
- Odile Marchetti will list the invoice corrections that finance needs to see in the reports.
