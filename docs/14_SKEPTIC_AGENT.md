# 14 - Skeptic Agent

## Purpose

Prevent premature convergence on a plausible story.

## Independence

The Skeptic should receive evidence and conclusions, but should not be instructed to preserve the current narrative.

## Attack checklist

- metric definition wrong?
- query/grain mismatch?
- stale or partial data?
- attribution change?
- seasonality/event confound?
- correlation mistaken for causation?
- omitted confounder?
- contradictory segment?
- forecast model out of domain/drifting?
- intervention unsupported by diagnosed cause?
- alternative explanation with similar evidence?

## Verdict

- PASS
- REVISE
- REJECT

## Re-open behavior

`REVISE` or `REJECT` creates explicit follow-up tasks, normally returning work to Observer/Diagnostic/Prediction rather than merely adding a disclaimer.
