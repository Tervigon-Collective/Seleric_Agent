---
name: config-change
description: Workflow for adding or changing a versioned configuration object in the Control Plane Service (BusinessNode, GoalDefinition, HealthPolicy, InterventionTemplate, IntentDefinition, etc.). Use whenever a ticket touches config schema or the draft/validate/approve/publish/rollback lifecycle.
---

# Configuration object change workflow

Seleric's business rules live in versioned PostgreSQL config, never
hardcoded in application code (`CLAUDE.md` Architecture Rules).

1. Find the object type in doc 10 §4 "Core Configuration Objects" and its
   full schema in `08_ADMIN_AND_CONFIGURATION_CONTROL_PLANE.md` /
   `06_LOW_LEVEL_DESIGN_OOP.md`.
2. Any new or changed config object must go through: draft → validate →
   approve → publish → rollback. Don't add a shortcut that writes directly
   to the published/active version.
3. Validation must catch the failure modes specific to that object — e.g.
   DAG/cycle checks for `BusinessNode`/`BusinessEdge`, eligibility policy
   completeness for `EligibilityPolicy`. Check doc 06 for the specific
   validation rules already specified.
4. If Appsmith needs a new form for this object, route to
   `control-plane-admin-agent` — Appsmith writes only through the Control
   Plane API, never direct table access.
5. Confirm rollback actually works for the new object type before calling
   the ticket done — a config change that can't roll back is a production risk.
