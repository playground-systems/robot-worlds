"""Search-before-create orchestration for asset providers and import adapters.

Search and import are explicit dependencies: a model's claim that an asset does
not exist is not a search result. Network/import errors never authorize creation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class AssetSearchIncomplete(RuntimeError):
    pass


class AssetIncompatible(ValueError):
    """A checked candidate cannot meet the requested asset's requirements."""


@dataclass(frozen=True)
class AssetCandidate:
    id: str
    source_url: str
    revision: str
    license: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AssetSearch:
    query: str
    sources: tuple[str, ...]
    candidates: tuple[AssetCandidate, ...]
    completed: bool
    evidence: dict = field(default_factory=dict)


@dataclass
class SourcedAsset:
    asset: Any
    decision: str
    search: AssetSearch
    selected: AssetCandidate | None
    rejected: list[dict]


def source_asset(
    query: str,
    *,
    search: Callable[[str], AssetSearch],
    import_asset: Callable[[AssetCandidate], Any],
    create_asset: Callable[[str, AssetSearch, list[dict]], Any],
) -> SourcedAsset:
    """Try existing assets first; create only after a completed, recorded lookup.

    Providers order candidates by fit. An import adapter must check appearance,
    license, dimensions, cavities and articulation as relevant to the request.
    It raises AssetIncompatible with a reason only for a checked incompatibility.
    A failed download, malformed response or unexpected import error propagates.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Asset lookup needs a nonempty query")
    report = search(query)
    if not isinstance(report, AssetSearch) or report.query != query:
        raise AssetSearchIncomplete("Asset search did not return evidence for this request")
    if not report.completed or not report.sources:
        raise AssetSearchIncomplete("Online asset search was incomplete; creation is not a fallback for lookup failure")
    rejected = []
    for candidate in report.candidates:
        if not all((candidate.id, candidate.source_url, candidate.revision, candidate.license)):
            raise AssetSearchIncomplete("Asset candidate is missing source, revision or license evidence")
        try:
            asset = import_asset(candidate)
        except AssetIncompatible as error:
            if not str(error).strip():
                raise ValueError("Rejected assets need a recorded compatibility reason") from error
            rejected.append({"id": candidate.id, "source_url": candidate.source_url, "reason": str(error)})
            continue
        if asset is None:
            raise ValueError("Import adapter returned no asset; this is not a compatibility decision")
        return SourcedAsset(asset, "reuse_or_adapt", report, candidate, rejected)
    asset = create_asset(query, report, rejected.copy())
    if asset is None:
        raise ValueError("Creation adapter returned no asset")
    return SourcedAsset(asset, "created_after_search", report, None, rejected)
