"""Live ads monitor: platform entities discovered every tick, judged by registered and user-written rules.

Layers (each depends only on the interfaces of the one below):
  core/      entity model, registries (adapters, rules, actions), statistics, custom-rule conditions, schema export
  adapters/  one SourceAdapter per ad platform (Meta first), reading through Seleric MCP
  rules/     built-in rules: performance (P0) and playbook compliance (P1)
  runner     the 5-minute tick: discover, run due rules, reconcile findings, narrate changes, heartbeat
"""
