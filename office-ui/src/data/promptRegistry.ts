/** Curated starting questions for the empty-state prompt registry. */

export type PromptDomain = "marketing" | "sales" | "products" | "operations" | "cross";
export type PromptLevel = "L1" | "L2" | "L3" | "L4" | "L5" | "L6" | "L7" | "L8+";
export type PromptTag = "successful" | "diagnosis" | "exec";

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
  { id: "exec", label: "Executive" },
  { id: "diagnosis", label: "Diagnosis" },
  { id: "all", label: "All" },
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
 * Demo set: only questions that passed live eval end to end (no simple lookups, nothing partial or flaky).
 * Executive prompts first, then further cross-domain drill-downs.
 */
export const PROMPT_REGISTRY: RegistryPrompt[] = [
  // ── Executive set: founder questions, each passed live eval (2026-10-29 UAT/BI/why runs and the 2026-10-30 founder sessions) ──
  {
    id: "uat-founder-week-vs-last",
    text: "How are we doing this week compared to last week? Give me the one-minute version.",
    domain: "operations",
    level: "L5",
    tags: ["successful", "exec"],
  },
  {
    id: "uat-founder-making-money",
    text: "Are we actually making money, or just buying revenue? Be honest.",
    domain: "cross",
    level: "L5",
    tags: ["successful", "exec"],
  },
  {
    id: "uat-ads-scale-or-kill",
    text: "Which Meta campaigns should I scale and which should I kill this week?",
    domain: "marketing",
    level: "L5",
    tags: ["successful", "exec"],
  },
  {
    id: "bi-views-no-sales",
    text: "Which products get lots of page views but few sales, and are we spending ad money on them?",
    domain: "products",
    level: "L5",
    tags: ["successful", "exec"],
  },
  {
    id: "why-orders-week-19-oct",
    text: "Why did we get fewer orders in the week of 19 October than the week before?",
    domain: "sales",
    level: "L7",
    tags: ["diagnosis", "successful", "exec"],
  },
  {
    id: "uat-bi-sept-walkthrough",
    text: "Walk me through September: how much did we sell, what did it cost us to get those sales, and what was left at the end?",
    domain: "cross",
    level: "L5",
    tags: ["successful", "exec"],
  },
  {
    id: "uat-meta-vs-finance-roas",
    text: "Meta says our ROAS is great but finance doesn't see it. What's going on?",
    domain: "marketing",
    level: "L6",
    tags: ["diagnosis", "successful", "exec"],
  },
  {
    id: "exec-customers-coming-back",
    text: "Are my customers coming back, or is it all first-time buyers?",
    domain: "sales",
    level: "L5",
    tags: ["successful", "exec"],
  },
  {
    id: "exec-new-customer-cost-vs-worth",
    text: "What does a new customer cost us versus what they're worth?",
    domain: "cross",
    level: "L5",
    tags: ["successful", "exec"],
  },
  {
    id: "exec-monday-actions",
    text: "If you were me, what are the three things I should do on Monday, and what's the number behind each?",
    domain: "cross",
    level: "L6",
    tags: ["successful", "exec"],
  },

  // ── More questions that passed live eval (cross-domain drill-downs) ──
  {
    id: "uat-web-buying-sources",
    text: "Which traffic sources bring people who actually buy, not just visit, and what are we paying for them?",
    domain: "marketing",
    level: "L6",
    tags: ["successful"],
  },
  {
    id: "uat-strategy-push-or-stop-products",
    text: "Which products should we push harder in ads and which should we stop advertising?",
    domain: "products",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "uat-strategy-month-read",
    text: "Give me a read on September: what worked, what didn't, and what should we change for November?",
    domain: "cross",
    level: "L6",
    tags: ["successful"],
  },
  {
    id: "uat-why-ads-cause-orders",
    text: "Do our ads actually cause more orders, or do the two just move together?",
    domain: "marketing",
    level: "L7",
    tags: ["diagnosis", "successful"],
  },
  {
    id: "bi-ads-chart-october",
    text: "Show me a chart of daily net sales against ad spend for October, and point out the days we spent a lot but sold little.",
    domain: "cross",
    level: "L4",
    tags: ["successful"],
  },
  {
    id: "uat-google-spend-week",
    text: "What did we spend on Google last week and what did we get for it? Does anything look off?",
    domain: "marketing",
    level: "L4",
    tags: ["successful"],
  },
  {
    id: "uat-funnel-dropoff-device",
    text: "Where are people dropping off between landing on the site and paying? Is mobile worse than desktop?",
    domain: "products",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "uat-product-page-drop",
    text: "Which product pages lose the most people?",
    domain: "products",
    level: "L4",
    tags: ["successful"],
  },
  {
    id: "bi-repeat-customer-share",
    text: "What share of our customers have ordered more than once, and are new customers or repeat customers worth more?",
    domain: "sales",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "bi-repeat-by-channel",
    text: "Which channel brings us the most repeat customers?",
    domain: "marketing",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "why-meta-spend-jump",
    text: "Why did Meta spend jump so much yesterday?",
    domain: "marketing",
    level: "L6",
    tags: ["diagnosis", "successful"],
  },
  // ── Passed live eval on 2026-10-13/15 and again on 2026-10-30 ──
  {
    id: "prev-meta-wasting-money",
    text: "Which Meta campaigns are wasting money?",
    domain: "marketing",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "prev-profitable-products",
    text: "Which products are profitable after returns and advertising costs?",
    domain: "products",
    level: "L5",
    tags: ["successful"],
  },
  {
    id: "prev-sales-drop-yesterday",
    text: "Why did my sales drop yesterday?",
    domain: "sales",
    level: "L6",
    tags: ["diagnosis", "successful"],
  },
  {
    id: "prev-cac-increase-drivers",
    text: "Why did CAC increase yesterday? Check whether the change came from CPM, CTR, CPC, landing-page conversion, checkout conversion, product mix, or channel mix.",
    domain: "marketing",
    level: "L7",
    tags: ["diagnosis", "successful"],
  },
  {
    id: "prev-finance-event-vs-order-date",
    text: "What was the finance P&L net profit for September 2026 as booked by event date, and how does it differ from the order-date net profit?",
    domain: "operations",
    level: "L6",
    tags: ["successful"],
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
    return prompt.tags.includes(chip);
  });
}

/** Empty-chat starters: the executive set. The Prompts browser holds the rest. */
export function frontChatPrompts(prompts: RegistryPrompt[] = PROMPT_REGISTRY): RegistryPrompt[] {
  return prompts.filter((prompt) => prompt.tags.includes("exec"));
}

export function groupByDomain(prompts: RegistryPrompt[]): { domain: PromptDomain; prompts: RegistryPrompt[] }[] {
  const order: PromptDomain[] = ["sales", "marketing", "products", "operations", "cross"];
  return order
    .map((domain) => ({ domain, prompts: prompts.filter((p) => p.domain === domain) }))
    .filter((group) => group.prompts.length > 0);
}
