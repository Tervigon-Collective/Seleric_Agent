# eval/cassettes

Recorded MCP traffic for the replay suite. One file per suite
(`<suite>.json`), keyed by the canonical hash of `capability + arguments`.

Record (or refresh) a cassette from a live MCP:

```bash
uv run python scripts/eval_run.py --mode record --suite golden
```

Replay reads this directory automatically:

```bash
uv run python scripts/eval_run.py --mode replay --suite golden
```

A replay request that is not in the cassette raises `CassetteMiss` and fails
the case — the suite never passes on traffic it did not record. Re-record the
cassette after the pipeline (not the catalogue) changes the queries it issues.
