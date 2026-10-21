"""Tool precondition thresholds ported from ``config/*_policies.yaml`` (A1.3).

Sprint 2 C disposition: migrate the gate numbers the V3 tools actually
enforce into this module. The five YAML files stay on disk until swarm_v2
callers stop importing them — do not delete YAML as part of the port.
"""

from __future__ import annotations

# ---- Diagnosis engine (causal/diagnosis.py) ---------------------------------
# Statistical conventions only. Nothing here names a metric, dimension, channel
# or business threshold: every candidate cause, identity and DAG edge comes from
# catalogue lineage plus the data, and these numbers only decide how much
# evidence is "enough".
DIAG_HISTORY_DAYS: int = 56            # daily history fetched before the event
DIAG_REFERENCE_WEEKS: int = 4          # same-weekday reference days per event day
DIAG_EVENT_Z: float = 2.0              # |z| at/above which the event is unusual
DIAG_EVENT_Z_NOTABLE: float = 1.5      # |z| at/above which it is notable (diagnosed, flagged moderate)
DIAG_OUTLIER_Z: float = 3.0            # robust z marking a history day abnormal (excluded from references)
DIAG_IDENTITY_TOLERANCE: float = 0.01  # relative dispersion allowed in a verified identity
# Additive accounting bridge (outcome = signed sum of same-unit additive metrics):
# every history day must reproduce within this share of the outcome's typical size.
DIAG_BRIDGE_TOLERANCE: float = 0.005
DIAG_BRIDGE_MAX_TERMS: int = 6         # terms in one level of the bridge
DIAG_BRIDGE_BUDGET_S: float = 8.0       # wall-clock cap on the whole bridge search
DIAG_BRIDGE_DEPTH: int = 4             # levels: outcome -> terms -> their terms -> …
DIAG_APPROX_IDENTITY_TOLERANCE: float = 0.1  # ... in an approximate (cross-system) identity
DIAG_COMEASURE_CV: float = 0.02        # X/Y this stable => same quantity, not a cause
DIAG_TREATMENT_CLUSTER_R: float = 0.9  # |r| grouping candidate treatments as inseparable
DIAG_ALPHA: float = 0.05               # CI / test level
DIAG_REFUTER_TOLERANCE: float = 0.25   # relative effect drift allowed by stability refuters
DIAG_MAX_DRIVERS: int = 8              # upstream candidates estimated (search_breadth adds)
DIAG_MAX_DIMENSIONS: int = 16          # dimensions screened for segment localisation
DIAG_MAX_SEGMENTS_REPORTED: int = 5
DIAG_LEVER_DEPTH: int = 2              # rate hops out from the outcome's parts (spend -> impressions -> clicks)
DIAG_LEVER_MAX_VOLUMES: int = 10
DIAG_LEVER_MAX_RATES: int = 12
DIAG_LEVER_SEGMENTS: int = 2           # segments of the localising dimension broken into levers
DIAG_MIN_ROWS_PER_COVARIATE: int = 3   # estimation rows needed per regressor
DIAG_PLACEBO_SHIFTS: int = 60          # circular-shift placebo draws
DIAG_BROAD_BASED_MAX: float = 0.3      # segment-specificity at/below which a change is broad-based
DIAG_COMEASURE_R: float = 0.95         # normal-day lockstep with the outcome => same units, not a cause
DIAG_UNIT_COUNT_R: float = 0.90        # normal-day lockstep with the count of the outcome's own units (orders for sales) => the same events
DIAG_MIN_DIMENSION_COVERAGE: float = 0.9  # share of the outcome a dimension's segments must account for
# Calendar words in a catalogue grain (``brand_order_day``): time buckets, not units.
DIAG_CALENDAR_GRAIN_TOKENS: tuple[str, ...] = ("hour", "day", "week", "month", "quarter", "year")

# From config/prediction_policies.yaml — history sufficiency (also used by
# anomaly/causal "enough rows" preconditions).
MIN_OBSERVATION_ROWS: int = 8

# From config/skeptic_policies.yaml — the EvidenceValidator's two signals
# (Sprint 3 C). An ADDITION, not a move: SkepticPolicies.load() still reads the
# YAML for agents/skeptic/*, so both must change together until that retires.
#
# These two numbers are the reason docs/BUG_SHEET.md #12 is coherent rather than
# a bug: STRONG (0.72) sits well above revise_below (0.55), so a claim can hold
# STRONG trust and still be told to REVISE by a gap/alternative/warning. Moving
# them closer together would quietly couple the two signals.
TRUST_LABEL_THRESHOLDS: dict[str, float] = {
    "INSUFFICIENT": 0.0,
    "WEAK": 0.35,
    "PROBABLE": 0.55,
    "STRONG": 0.72,
    "VERIFIED": 0.9,
}
TRUST_REVISE_BELOW: float = 0.55


WARN_THIN_HISTORY: str = "policy:thin_history"

WARN_MODEL_UNAVAILABLE: str = "policy:model_unavailable"
WARN_MODEL_FIT_FAILED: str = "policy:model_fit_failed"
WARN_INVALID_HORIZON: str = "policy:invalid_horizon"

# ---- Sprint 4 C: breakdowns, knowledge, experiments -------------------------

# Funnel membership and order are NOT declared here.
# A funnel's stages belong to the catalogue, which types each metric as a
# count or a ratio; analytics/funnel.py derives the base stage and the step
# order from that typing plus the measured rates. Listing metric ids here
# would pin one funnel's shape into the harness and silently exclude any
# other funnel the catalogue grows.


# ---- Entity audit (toolsets/entity_audit.py) --------------------------------
# Which catalogue measures play which role when a set of entities (campaigns, products, channels) is audited, in order of
# preference. These are catalogue ids, so they live here with the other policy, not in a prompt. Thresholds are how much
# evidence makes an entity a "winner" or a "loser" rather than "too early to say".
AUDIT_SPEND_MEASURES: tuple[str, ...] = ("ad_spend", "product_ad_spend", "order_pnl_ad_spend")
AUDIT_RETURN_MEASURES: tuple[str, ...] = ("funnel_order_revenue", "order_pnl_net_sales", "net_sales", "product_net_revenue", "funnel_revenue")
AUDIT_ROAS_MEASURES: tuple[str, ...] = ("product_net_roas", "net_roas", "product_gross_roas", "gross_roas")  # return = ratio x spend when no revenue measure came
AUDIT_ORDER_MEASURES: tuple[str, ...] = ("orders", "funnel_order_purchases", "funnel_purchases", "product_orders")
AUDIT_SIGNAL_MEASURES: tuple[str, ...] = ("ctr", "hook_rate", "link_clicks", "landing_page_views")  # attention that should convert
AUDIT_MIN_ORDERS: int = 5            # fewer orders than this and a good or bad ratio is "early", not a verdict
AUDIT_TOP_N: int = 3                 # entities named per list (best, worst, concentration)
AUDIT_LEAK_LIST: int = 5             # entities named in the no-orders list, with their spend (the question is often a ranking)

# ---- Exploration engine (exploration/engine.py) -----------------------------
# Statistical conventions for open-ended exploration. As with the diagnosis
# numbers above, nothing here names a metric, dimension or business threshold:
# they decide how much evidence makes a pattern worth reporting.
EXPLORE_FDR: float = 0.10                 # Benjamini-Hochberg level across every test a run performs
EXPLORE_HISTORY_WINDOWS: int = 6          # equal-length windows before the current one (the "normal" variation)
EXPLORE_MAX_SPAN_DAYS: int = 400          # cap on fetched history (windows x window length)
# A percent change beyond this is an artefact of a near-zero base ("refunds +5,851.7%"), not information: the figures are
# stated instead and no percent is printed (business_snapshot, diagnosis and exploration statements).
MAX_REPORTABLE_PCT_CHANGE: float = 1000.0
EXPLORE_DEFAULT_WINDOW_DAYS: int = 7      # window when the question names no period
EXPLORE_MAX_WINDOW_DAYS: int = 92
EXPLORE_MIN_RELATIVE_CHANGE: float = 0.03  # smallest window-over-window change worth reporting
EXPLORE_MIN_TREND_PER_WEEK: float = 0.01   # smallest trend (share of level per week) worth reporting
EXPLORE_MIN_SHIFT: float = 0.05            # smallest level shift worth reporting
EXPLORE_MIN_SHARE_MOVE: float = 0.02     # smallest change in a segment's share of the total worth reporting
EXPLORE_MIN_TOP_SHARE: float = 0.3         # an "outstanding" segment must carry this share of the total
EXPLORE_MIN_ABS_CORRELATION: float = 0.4   # weakest day-to-day co-movement worth reporting
EXPLORE_ADTRIBUTOR_T_EEP: float = 0.1      # per-segment share of the change (Adtributor T_EEP)
EXPLORE_ADTRIBUTOR_T_EP: float = 0.67      # cumulative share the set must explain (Adtributor T_EP)
EXPLORE_MAX_SEGMENT_ROWS: int = 6000       # skip a breakdown whose daily rows exceed this
# Ranking prior per insight kind: what moved now outranks a structural fact the
# operator already knows ("the biggest channel is the biggest").
EXPLORE_KIND_PRIOR: dict[str, float] = {
    "period_change": 1.0,
    "distribution_shift": 1.0,
    "change_point": 0.9,
    "trend": 0.8,
    "co_movement": 0.6,
    "outstanding": 0.5,
}
# depth -> (headline metrics, dimensions per metric, insights reported)
EXPLORE_DEPTH: dict[str, tuple[int, int, int]] = {
    "scan": (3, 2, 5),
    "focus": (5, 3, 8),
    "deep": (8, 5, 12),
}


