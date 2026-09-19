# Knowledge corpus

**This directory is intentionally empty of documents.**

`toolsets/knowledge.py::search_knowledge` retrieves over the markdown files
placed here. Nothing in Seleric writes them — they are operator documents,
authored by people. Shipping invented incidents or SOPs to make the directory
look populated would be fabricating institutional knowledge, so the retrieval
works and the corpus starts empty.

An empty corpus is reported as a *miss*, not an error: `search_knowledge`
returns `success=True` with a `policy:empty_knowledge_corpus` warning and says
plainly that nothing has been loaded. That is deliberately distinguishable
from "documents exist, none match your query", because those tell you
different things.

## Adding a document

Drop a `.md` file in this directory (subdirectories are fine — the loader
recurses). This `README.md` is skipped, as is any file starting with `README`.

```markdown
---
type: incident
---

# Checkout conversion dropped after the 2026-08 payment migration

On 2026-08-14 `metric.purchase_cvr` fell from ~2.1% to ~1.4% ...
```

- **`type:`** — one of `incident`, `sop`, `model_card`, `experiment_note`.
  Anything else is kept but labelled `note`; the document is never dropped for
  having an unrecognized type.
- **Front matter is optional.** Without it the document is a `note`.
- **Title** comes from the first `#` heading, falling back to the filename.

## What this is not

It is **not** a metric store. Non-negotiable rule 12: knowledge retrieval never
substitutes for a Cube query on a live metric value. `search_knowledge` writes
no artifact, so nothing it returns can be cited as evidence for a numeric
claim — a number in a document here is a historical note, not a measurement.

If you want the current value of something, that is `query_metrics`.
