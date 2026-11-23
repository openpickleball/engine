"""
Network connectivity: the contact graph and its connected components.

Reference implementation: outputs are not OPEN ratings, and ratings are
provisional (see "Status" in docs/behavior.md).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from ._config import Config
from ._math import _latest
from ._model import NetworkConnectivity, Player
from ._types import RatingFormat
from ._validation import _days_before

# Connectivity results kept per engine, by (format, reference date).
_CONNECTIVITY_CACHE_SIZE = 16


class _UnionFind:
    """Groups of ids. Each group's root is its smallest id, so the result is order-free."""

    def __init__(self, ids: Iterable[str]) -> None:
        self.parent = {pid: pid for pid in ids}

    def find(self, pid: str) -> str:
        root = pid
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[pid] != root:
            self.parent[pid], pid = root, self.parent[pid]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            low, high = sorted((root_a, root_b))
            self.parent[high] = low

    def sizes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for pid in sorted(self.parent):
            root = self.find(pid)
            out[root] = out.get(root, 0) + 1
        return out


def _contact_graph(
    players: Mapping[str, Player], rating_format: RatingFormat, cutoff: date
) -> dict[str, dict[str, date]]:
    """
    Each player with a state in the format, to their contacts dated on or after cutoff.

    Only players with a state in the format are nodes, and a contact's
    date is the later of the two states' dates for each other.
    """
    graph: dict[str, dict[str, date]] = {}
    for pid in sorted(players):
        if rating_format in players[pid].ratings:
            graph[pid] = {}
    for pid in sorted(graph):
        state = players[pid].ratings[rating_format]
        for contacts in (state.partners, state.opponents):
            for other in sorted(contacts):
                on = contacts[other]
                if other == pid or other not in graph or on < cutoff:
                    continue
                for a, b in ((pid, other), (other, pid)):
                    graph[a][b] = _latest(graph[a].get(b), on)
    return graph


def _link_contacts(
    graph: dict[str, dict[str, date]],
    ids: list[str],
    network: _UnionFind,
    pools: _UnionFind,
    cfg: Config,
    minimum_days: int,
) -> None:
    """
    _network_connectivity's linking pass: join contacts into groups and tied players into pools.

    Over each pair of ids in contact, in sorted order (u before v), it
    joins them in network, then joins them in pools when they share at
    least connectivity_min_shared_contacts contacts and each reached those
    shared contacts on at least minimum_days distinct dates.
    """
    for u in ids:
        row = graph[u]
        for v in sorted(row):
            if v <= u:
                continue
            network.union(u, v)
            other = graph[v]
            shared = row.keys() & other.keys()
            if len(shared) < cfg.connectivity_min_shared_contacts:
                continue
            # Each of them reached the shared contacts on enough dates, so,
            # with a minimum of 2 or more, a visitor for one day (all of
            # their dates equal) never ties.
            if len({row[w] for w in shared}) >= minimum_days and (
                len({other[w] for w in shared}) >= minimum_days
            ):
                pools.union(u, v)


def _network_connectivity(
    players: Mapping[str, Player], rating_format: RatingFormat, reference: date, cfg: Config
) -> tuple[dict[str, NetworkConnectivity], int]:
    """
    NetworkConnectivity of every player with a state in the format, and the largest group size.

    Every loop runs over sorted ids, every group is a union-find with its
    smallest id as root, and sets are read only for their sizes, so the
    result depends only on the stored states, the config, and the
    reference date.
    """
    cutoff = _days_before(reference, cfg.diversity_window_days)
    graph = _contact_graph(players, rating_format, cutoff)
    ids = sorted(graph)
    network = _UnionFind(ids)
    pools = _UnionFind(ids)
    minimum_days = cfg.connectivity_min_contact_days
    _link_contacts(graph, ids, network, pools, cfg, minimum_days)

    group_sizes = network.sizes()
    largest = max(group_sizes.values(), default=0)
    members: dict[str, list[str]] = {}
    for pid in ids:
        members.setdefault(pools.find(pid), []).append(pid)
    out: dict[str, NetworkConnectivity] = {}
    for root in sorted(members):
        pool = members[root]
        in_pool = set(pool)
        boundary = {w for u in pool for w in graph[u] if w not in in_pool}
        near = in_pool | boundary
        fringe = {w for w in boundary if all(x in near for x in graph[w])}
        outside = boundary - fringe
        outside_days = {graph[u][w] for u in pool for w in graph[u] if w in outside}
        group_size = group_sizes[network.find(root)]
        in_main = 2 * group_size >= largest
        connected = (
            (len(pool) > 1 or bool(boundary))
            and in_main
            and (
                2 * (len(pool) + len(fringe)) > group_size
                or (
                    len(outside) >= cfg.connectivity_min_outside_players
                    and len(outside_days) >= minimum_days
                )
            )
        )
        for pid in pool:
            out[pid] = NetworkConnectivity(
                pool_size=len(pool),
                fringe_players=len(fringe),
                outside_players=len(outside),
                outside_days=len(outside_days),
                group_size=group_size,
                in_main_network=in_main,
                connected=connected,
            )
    return out, largest
