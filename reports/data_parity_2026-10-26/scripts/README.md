Reference only. These are the scripts that produced data_parity.xlsx on 2026-10-26.
They read scratch JSON from a session folder (out/*.json) and use absolute paths, and they take
ClickHouse/Trino credentials from /opt/seleric/mage-ai/.env (never printed). Order: s1 inventory,
s2b parity, s3b sync, s4 platform, s6/s6b chains, s7b matrix, s8 ClickHouse checks, s9/s9b extras,
s11 daily, s12 ad-level, s13 payments, run_core.sh (Core certifications), s10_build.py (workbook + csv).
