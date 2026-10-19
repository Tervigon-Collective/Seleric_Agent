"""Values holding "|" and partial breakdowns (live 2026-10-08).

MS3-ed23dd03e2: the Google campaign "[Google Build] PMax - Seasonal New | 27th May" split its Markdown row, every
later column shifted one to the left, and the answer ranked a 21,776 "net ROAS" that was the campaign's ad spend.
The merge also joined 2 of 32 campaigns because one evidence id per metric was cited.
"""

from __future__ import annotations

PMAX = "[Google Build] PMax - Seasonal New | 27th May"
























def test_an_unavailable_snapshot_does_not_shadow_the_last_good_one(tmp_path) -> None:
    # live 2026-10-09: an empty business snapshot dated today was read instead of yesterday's full one
    from seleric_swarm.services.domain_health.models import DomainStateSnapshot
    from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

    store = SnapshotStore(base_dir=tmp_path)
    good = DomainStateSnapshot(domain="business", brand_id="20", as_of="2026-10-08", computed_at="2026-10-09T05:57:36",
                               window={}, status="OK", metrics=[], headline_signals=["x"], provenance={})
    empty = good.model_copy(update={"as_of": "2026-10-09", "status": "UNAVAILABLE", "headline_signals": []})
    store.save(good)
    store.save(empty)
    assert store.get_latest("business").as_of == "2026-10-08"
