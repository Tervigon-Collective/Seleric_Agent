"""The exact additive bridge for WHAT: an outcome that is a signed sum of up to six same-unit metrics, found by meeting
in the middle on random projections and verified on the full daily vectors (moved from ``causal/diagnosis.py``,
plan P2.2). The search has a time budget read from an injected clock."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date
from itertools import combinations
from typing import Any

from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.numeric import Series, _finite


class _BridgeSpace:
    """Every candidate's daily vector, plus random projections of every signed sum
    of up to three of them, built once per diagnosis.

    An exact identity ``target = Σ sign × metric`` (up to six terms) is found by
    meeting in the middle: the projection of one signed sum of ≤3 terms must equal
    the target's projection minus another's. Exhaustive, not greedy — greedy
    searches were tried first and failed live: ranking partial sums by what is left
    fails on a small difference of large quantities (net profit), and ranking by
    least-squares fit never reached net COGS behind the many collinear revenue
    measures. Every projected match is verified on the full daily vectors.
    """

    _GROUP = 3  # terms per half: identities of up to 2 × _GROUP terms

    def __init__(self, names: list[str], days: list[date], series: dict[str, Series]) -> None:
        import numpy as np

        self.np = np
        self.days = days
        self.names = names
        self.X = np.column_stack([
            np.array([float(series[c][d]) if _finite(series.get(c, {}).get(d)) else 0.0 for d in days])
            for c in names
        ]) if names else np.zeros((len(days), 0))
        rng = np.random.default_rng(20261007)  # fixed: the same data give the same answer
        # Four projections: the first orders the search, the other three reject
        # coincidental matches in bulk before any per-candidate check.
        self.W = rng.normal(size=(len(days), 4))
        self.px = self.X.T @ self.W  # (k, 4)
        self.groups: dict[int, tuple[Any, Any, Any]] = {}
        k = len(names)
        for size in range(1, self._GROUP + 1):
            combos = np.array(list(combinations(range(k), size)), dtype=int).reshape(-1, size)
            signs = np.array(list(_sign_patterns(size)), dtype=float)
            if not len(combos):
                continue
            idx = np.repeat(combos, len(signs), axis=0)
            sg = np.tile(signs, (len(combos), 1))
            proj = np.einsum("ns,nsk->nk", sg, self.px[idx])
            order = np.argsort(proj[:, 0])
            self.groups[size] = (idx[order], sg[order], proj[order])

    def find(
        self, y: Any, tol: float, allowed: Any, max_terms: int, *, positive_only: bool = False,
        deadline: float | None = None, clock: Callable[[], float],
    ) -> list[tuple[int, float]] | None:
        """The smallest exact identity for ``y`` over candidates where ``allowed``
        (a plain sum of parts when ``positive_only``); None past ``deadline`` (seconds on ``clock``)."""
        np = self.np
        py = y @ self.W
        # A residual of at most ``tol`` a day projects to about tol × ‖w‖₂; 4σ.
        eps = 4.0 * tol * np.linalg.norm(self.W, axis=0) + 1e-6
        for total in range(2, max_terms + 1):
            best: tuple[float, list[tuple[int, float]]] | None = None
            for a in range(1, min(self._GROUP, total - 1) + 1):
                b = total - a
                if b < a or b > self._GROUP or a not in self.groups or b not in self.groups:
                    continue
                ia, sa, pa = self.groups[a]
                ib, sb, pb = self.groups[b]
                if positive_only:
                    keep = (sb > 0).all(axis=1)
                    ib, sb, pb = ib[keep], sb[keep], pb[keep]
                okA = allowed[ia].all(axis=1) & ((sa > 0).all(axis=1) if positive_only else True)
                okB = allowed[ib].all(axis=1)
                ib, sb, pb = ib[okB], sb[okB], pb[okB]
                ia, sa, pa = ia[okA], sa[okA], pa[okA]
                if not len(ia) or not len(ib):
                    continue
                want = py[None, :] - pa
                lo = np.searchsorted(pb[:, 0], want[:, 0] - eps[0])
                counts = np.searchsorted(pb[:, 0], want[:, 0] + eps[0]) - lo
                rows = np.nonzero(counts)[0]
                for chunk in np.array_split(rows, max(1, int(counts[rows].sum() // 200_000) + 1)):
                    if deadline is not None and clock() > deadline:
                        return None
                    c = counts[chunk]
                    if not c.sum():
                        continue
                    ra = np.repeat(chunk, c)
                    jb = np.repeat(lo[chunk], c) + (np.arange(int(c.sum())) - np.repeat(np.cumsum(c) - c, c))
                    close = np.all(np.abs(pb[jb, 1:] - want[ra, 1:]) <= eps[1:], axis=1)
                    for r, j in zip(ra[close], jb[close], strict=True):
                        terms = list(ia[r]) + list(ib[j])
                        if len(set(terms)) != total:
                            continue
                        signs = list(sa[r]) + list(sb[j])
                        worst = float(np.max(np.abs(y - self.X[:, terms] @ np.array(signs))))
                        if worst <= tol and (best is None or worst < best[0]):
                            best = (worst, [(int(t), float(g)) for t, g in zip(terms, signs, strict=True)])
            if best is not None:
                return best[1]
        return None


def _sign_patterns(size: int) -> list[tuple[float, ...]]:
    out: list[tuple[float, ...]] = [()]
    for _ in range(size):
        out = [(*p, s) for p in out for s in (1.0, -1.0)]
    return out


def discover_bridge_tree(
    target: str, candidates: list[str], series: dict[str, Series], days: list[date], depth: int | None = None, *, cfg: DiagnosisConfig,
    clock: Callable[[], float],
) -> dict[str, list[tuple[str, float]]]:
    """``{metric: its verified terms}`` for the target and, one level at a time, for
    each term that is itself an exact sum of the remaining candidates.

    Candidates are proposed, the data decide: a signed sum counts only if it
    reproduces the metric on every history day to within DIAG_BRIDGE_TOLERANCE of
    its typical size, which a coincidence cannot do over weeks of days. A
    candidate equal to the metric on its own is a twin, not a component.

    Netting happens only at the top. Every identity can be rearranged (ad spend
    = gross sales − gross profit − gross COGS holds because gross profit is defined
    net of ad spend; gross sales = gross profit + gross COGS + P&L margin − P&L
    profit holds too, live 2026-10-08), and the data alone cannot tell a part from
    a whole that contains it. So a P&L is read the way it is built: the outcome and
    a term that adds to it may be revenue minus deductions (profit = margin − ad
    spend; margin = gross sales − discounts − returns − COGS); every other line —
    and any term that is deducted, a cost — is split only into a plain sum of its
    parts (net COGS = product cost + operating cost; operating cost = shipping +
    packaging + gateway fees + RTO).
    """
    import numpy as np

    use = [d for d in days if _finite(series.get(target, {}).get(d))]
    if len(use) < cfg.min_observation_rows:
        return {}
    names = [
        c for c in dict.fromkeys([target, *candidates])
        if sum(1 for d in use if _finite(series.get(c, {}).get(d))) >= cfg.min_observation_rows
    ]
    if target not in names:
        return {}
    space = _BridgeSpace(names, use, series)
    index = {n: i for i, n in enumerate(names)}
    limit = cfg.bridge_max_terms

    def tol_of(i: int) -> float:
        return max(1.0, cfg.bridge_tolerance * float(np.median(np.abs(space.X[:, i]))))

    def allowed_for(i: int, excluded: set[int]) -> Any:
        y = space.X[:, i]
        mask = np.ones(len(names), dtype=bool)
        for j in excluded | {i}:
            mask[j] = False
        for j in range(len(names)):
            col = space.X[:, j]
            if not col.any() or float(np.max(np.abs(col - y))) <= tol_of(i):
                mask[j] = False  # empty, or a twin of the metric
        return mask

    deadline = clock() + cfg.bridge_budget_s
    tree: dict[str, list[tuple[str, float]]] = {}
    frontier = [(index[target], 1.0)]
    seen = {index[target]}
    for level in range(cfg.bridge_depth if depth is None else depth):
        nxt: list[tuple[int, float]] = []
        for node, effective in frontier:
            allowed = allowed_for(node, seen)
            y, tol = space.X[:, node], tol_of(node)
            terms = space.find(y, tol, allowed, limit, positive_only=True, deadline=deadline, clock=clock) if level > 0 else None
            if terms is None and (level == 0 or (level == 1 and effective > 0)):
                terms = space.find(y, tol, allowed, limit, deadline=deadline, clock=clock)
            if not terms:
                continue
            tree[names[node]] = [(names[t], g) for t, g in terms]
            for t, g in terms:
                seen.add(t)
                nxt.append((t, effective * g))
        frontier = nxt
    return tree


def shapley(f: Any, x0: list[float], x1: list[float]) -> list[float]:
    """Exact Shapley attribution of ``f(x1) - f(x0)`` to each input."""
    n = len(x0)
    out = [0.0] * n
    for i in range(n):
        others = [j for j in range(n) if j != i]
        for r in range(len(others) + 1):
            w = math.factorial(r) * math.factorial(n - r - 1) / math.factorial(n)
            for subset in combinations(others, r):
                base = [x1[j] if j in subset else x0[j] for j in range(n)]
                with_i = list(base)
                with_i[i] = x1[i]
                out[i] += w * (f(with_i) - f(base))
    return out
