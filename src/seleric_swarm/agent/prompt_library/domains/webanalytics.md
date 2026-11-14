---
views: [web_sessions, web_funnel, web_events, web_event_detail, funnel_orders, pdp_visits, pdp_section_reach, pdp_interaction_log, listing_card_events]
---
**Funnel / web analytics** (Snowplow tracking)
- Web purchases and revenue come from tracking and are a different basis from Shopify orders: label each, never mix them.
- A product-page behaviour analysis uses pdp_visits (funnel level), pdp_section_reach (reach, dwell by section) and pdp_interaction_log (clicks) together.
- The product-page and listing trackers only have data from their start; earlier periods are "no data", not zero. listing_card_events is empty until its theme flag is on.
