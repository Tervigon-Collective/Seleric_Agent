# 25 - Data Contracts

## Evidence data contract

All business observations should be normalized into an EvidenceArtifact before being treated as facts.

## Required dimensions

At minimum support:

- time range,
- timezone,
- source/platform,
- account/store,
- campaign/adset/ad where relevant,
- product/SKU where relevant,
- device/channel where relevant.

## Late-arriving data

Each source must define:

- expected freshness,
- attribution delay,
- mutable lookback window,
- reconciliation policy.

## Reproducibility

Store query/arguments hashes and calculation/version identifiers sufficient to reproduce a result.
