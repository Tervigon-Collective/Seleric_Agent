/** Curated starting questions for the empty-state prompt registry. */

export type PromptDomain = "marketing" | "sales" | "products" | "operations" | "cross";
export type PromptLevel = "L1" | "L2" | "L3" | "L4" | "L5" | "L6" | "L7" | "L8+";
export type PromptTag = "required" | "successful" | "diagnosis" | "golden";

export type RegistryPrompt = {
  id: string;
  text: string;
  domain: PromptDomain;
  level: PromptLevel;
  tags: PromptTag[];
};

export const DOMAIN_LABELS: Record<PromptDomain, string> = {
  marketing: "Marketing",
  sales: "Sales & revenue",
  products: "Products & conversion",
  operations: "Operations",
  cross: "Cross-domain",
};

export const FILTER_CHIPS = [
  { id: "all", label: "All" },
  { id: "L1", label: "L1 lookup" },
  { id: "required", label: "Required" },
  { id: "successful", label: "Successful" },
  { id: "diagnosis", label: "Diagnosis" },
  { id: "golden", label: "Golden" },
] as const;

export type FilterChipId = (typeof FILTER_CHIPS)[number]["id"];

export const DOMAIN_FILTERS: { id: "all" | PromptDomain; label: string }[] = [
  { id: "all", label: "All domains" },
  { id: "marketing", label: DOMAIN_LABELS.marketing },
  { id: "sales", label: DOMAIN_LABELS.sales },
  { id: "products", label: DOMAIN_LABELS.products },
  { id: "operations", label: DOMAIN_LABELS.operations },
  { id: "cross", label: DOMAIN_LABELS.cross },
];

/**
 * L1 lookups first (simple metric reads), then golden / diagnosis / required
 * coverage drawn from the live golden Q1–18 set and stress ladder.
 */
export const PROMPT_REGISTRY: RegistryPrompt[] = [
  // ── L1 lookups (required baseline) ──────────────────────────────────────
  {
    id: "l1-net-sales-yesterday",
    text: "What were net sales yesterday?",
    domain: "sales",
    level: "L1",
    tags: ["required", "successful", "golden"],
  },
  {
    id: "l1-gross-sales-yesterday",
    text: "What were gross sales yesterday?",
    domain: "sales",
    level: "L1",
    tags: ["required", "successful"],
  },
  {
    id: "l1-net-sales-last-month",
    text: "What were net sales last month?",
    domain: "sales",
    level: "L1",
    tags: ["required", "successful"],
  },
  {
    id: "l1-ad-spend-yesterday",
    text: "What was ad spend yesterday?",
    domain: "marketing",
    level: "L1",
    tags: ["required", "successful"],
  },
  {
    id: "l1-orders-yesterday",
    text: "How many orders did we get yesterday?",
    domain: "sales",
    level: "L1",
    tags: ["required", "successful"],
  },
  {
    id: "l1-multi-metric-yesterday",
    text: "What were net sales, orders, and ad spend yesterday?",
    domain: "cross",
    level: "L1",
    tags: ["required", "successful", "golden"],
  },

  // ── Golden questions (user-perspective, Q3–Q18) ─────────────────────────
  {
    id: "golden-q3-daily-series",
    text: "Give me net sales for each of the last 7 days.",
    domain: "sales",
    level: "L6",
    tags: ["golden", "successful"],
  },
  {
    id: "golden-q4-business-status",
    text: "What is the current business status for the last 7 completed days?",
    domain: "operations",
    level: "L5",
    tags: ["golden", "required"],
  },
  {
    id: "golden-q5-period-compare",
    text: "Compare net sales, orders, CAC, MER, Net ROAS, and net profit for the last 7 days versus the previous 7 days. Show absolute and percentage change.",
    domain: "cross",
    level: "L5",
    tags: ["golden", "successful", "required"],
  },
  {
    id: "golden-q6-month-compare",
    text: "Compare this month to the same number of days last month.",
    domain: "sales",
    level: "L3",
    tags: ["golden", "successful", "required"],
  },
  {
    id: "golden-q7-daily-best-worst",
    text: "Show the last 30 days of daily net sales, ad spend, MER, CAC, and net profit and identify the best and worst 3 days.",
    domain: "cross",
    level: "L6",
    tags: ["golden", "successful"],
  },
  {
    id: "golden-q8-top-products",
    text: "Which products generated the most net sales in the last 14 days? Give units sold, orders, net sales, refunds, and return rate.",
    domain: "products",
    level: "L4",
    tags: ["golden", "successful"],
  },
  {
    id: "golden-q9-meta-campaigns",
    text: "Which Meta campaigns performed best in the last 7 days? Rank them using spend, revenue, ROAS, CAC, CTR, CPC, CPM, clicks, LPVs, and purchases.",
    domain: "marketing",
    level: "L5",
    tags: ["golden", "successful"],
  },
  {
    id: "golden-q10-campaign-trends",
    text: "For the top 5 Meta campaigns from the last 7 days, show their daily CTR, CPC, CPM, spend, purchases, and ROAS trends.",
    domain: "marketing",
    level: "L6",
    tags: ["golden", "successful"],
  },
  {
    id: "golden-q11-revenue-vs-profit",
    text: "Which products are driving revenue but destroying profit in the last 30 days?",
    domain: "products",
    level: "L5",
    tags: ["golden", "successful"],
  },
  {
    id: "golden-q12-margin-tradeoff",
    text: "Find products whose sales increased versus the previous 30 days but whose contribution margin deteriorated. Explain the trade-off.",
    domain: "products",
    level: "L8+",
    tags: ["golden"],
  },
  {
    id: "golden-q13-sales-fall",
    text: "Why did sales fall yesterday compared with the average of the previous 7 comparable days? Break the change into traffic, conversion, AOV, cancellations, returns, and paid-media performance.",
    domain: "sales",
    level: "L7",
    tags: ["golden", "diagnosis", "required"],
  },
  {
    id: "golden-q14-cac-drivers",
    text: "Why did CAC increase yesterday? Check whether the change came from CPM, CTR, CPC, landing-page conversion, checkout conversion, product mix, or channel mix. Rank the drivers by likely contribution.",
    domain: "marketing",
    level: "L7",
    tags: ["golden", "diagnosis", "successful", "required"],
  },
  {
    id: "golden-q15-profit-bridge",
    text: "On 2026-10-02 we recorded a loss. Quantify exactly how much of the profit deterioration versus 2026-10-01 came from lower revenue, advertising cost, COGS, discounts, refunds, shipping, payment costs, and other available cost components. Reconcile the components back to the total profit change.",
    domain: "operations",
    level: "L7",
    tags: ["golden", "diagnosis"],
  },
  {
    id: "golden-q16-loss-contributors",
    text: "For 2026-10-02, identify which campaigns, products, and channels contributed most to the loss. Do not call correlation causal unless supported. Separate arithmetic contributors from statistically supported causal drivers.",
    domain: "cross",
    level: "L8+",
    tags: ["golden", "diagnosis"],
  },
  {
    id: "golden-q17-product-attribution",
    text: "Analyze Pawveralls Suspender Boots for the last 60 days. Show sales by Meta campaign, Google sub-channel, organic, WhatsApp, direct, and other sources. Reconcile attributed revenue against total product revenue and quantify unattributed revenue.",
    domain: "products",
    level: "L8+",
    tags: ["golden"],
  },
  {
    id: "golden-q18-blended-roas",
    text: "Which Meta campaigns appear profitable at platform ROAS but become unprofitable when evaluated using blended net sales, returns, discounts, COGS, and contribution margin?",
    domain: "marketing",
    level: "L8+",
    tags: ["golden", "successful"],
  },

  // ── Extra diagnosis / domain starters (stress + UI chips) ───────────────
  {
    id: "diag-roas-decline",
    text: "Why did ROAS decline compared with last week?",
    domain: "marketing",
    level: "L7",
    tags: ["diagnosis", "successful"],
  },
  {
    id: "mkt-campaign-revenue",
    text: "Which campaigns contributed the most revenue last week?",
    domain: "marketing",
    level: "L4",
    tags: ["successful"],
  },
  {
    id: "sales-source-yesterday",
    text: "Where did our sales come from yesterday?",
    domain: "sales",
    level: "L4",
    tags: ["successful"],
  },
  {
    id: "cross-spend-revenue-profit",
    text: "Compare marketing spend, revenue, and profitability.",
    domain: "cross",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "prod-losing-money",
    text: "Which products are losing money after ads and returns?",
    domain: "products",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "prod-checkout-conversion",
    text: "What changed in checkout conversion over the last 7 days?",
    domain: "products",
    level: "L6",
    tags: ["diagnosis", "successful"],
  },
];

export function filterPrompts(
  prompts: RegistryPrompt[],
  chip: FilterChipId,
  domain: "all" | PromptDomain,
): RegistryPrompt[] {
  return prompts.filter((prompt) => {
    if (domain !== "all" && prompt.domain !== domain) return false;
    if (chip === "all") return true;
    if (chip === "L1") return prompt.level === "L1";
    return prompt.tags.includes(chip);
  });
}

export function groupByDomain(prompts: RegistryPrompt[]): { domain: PromptDomain; prompts: RegistryPrompt[] }[] {
  const order: PromptDomain[] = ["sales", "marketing", "products", "operations", "cross"];
  return order
    .map((domain) => ({ domain, prompts: prompts.filter((p) => p.domain === domain) }))
    .filter((group) => group.prompts.length > 0);
}
