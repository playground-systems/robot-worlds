"""Bounded discovery of versioned assets in the public Gazebo Fuel catalog.

Discovery supplies provenance, not a compatibility verdict. Import adapters must
still check licensing, geometry and required articulation before selecting an asset.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .asset_sourcing import AssetCandidate, AssetSearch, AssetSearchIncomplete

FUEL_API = "https://fuel.gazebosim.org/1.0"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def search_online_assets(query: str, *, client: httpx.Client | None = None,
                         max_pages: int = 3, max_candidates: int = 30,
                         timeout: float = 30) -> AssetSearch:
    """Search Fuel using an explicit object query (for example, ``bowl``).

    The returned search scope is Fuel, not the whole internet. Hitting a page,
    candidate, response-size or time limit raises AssetSearchIncomplete. Missing
    revisions/license metadata also fail closed. Nothing is downloaded/executed
    beyond catalog JSON. A supplied client makes the same path testable offline.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValueError("Asset query must contain 1–200 characters")
    for name, value, upper in (("max_pages", max_pages, 10), ("max_candidates", max_candidates, 100)):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= upper:
            raise ValueError(f"{name} must be between 1 and {upper}")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
        raise ValueError("timeout must be positive and at most 120 seconds")
    if client is None:
        with httpx.Client(follow_redirects=False) as owned:
            return search_online_assets(query, client=owned, max_pages=max_pages,
                                        max_candidates=max_candidates, timeout=timeout)
    deadline = time.monotonic() + timeout
    requests = []

    def fetch(url, params=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssetSearchIncomplete("Fuel lookup exceeded its time budget")
        with client.stream("GET", url, params=params, timeout=min(10, remaining),
                           follow_redirects=False) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise AssetSearchIncomplete("Fuel response exceeded its size limit")
                if time.monotonic() >= deadline:
                    raise AssetSearchIncomplete("Fuel lookup exceeded its time budget")
                body.extend(chunk)
            requests.append(str(response.url))
            # Parse only the bounded body; response.json() would read the stream again.
            return json.loads(body), response.links

    candidates, seen = [], set()
    try:
        for page in range(1, max_pages + 1):
            rows, links = fetch(FUEL_API + "/models", {"q": query, "per_page": 20, "page": page})
            if not isinstance(rows, list):
                raise AssetSearchIncomplete("Fuel returned an invalid model list")
            for row in rows:
                if not isinstance(row, dict):
                    raise AssetSearchIncomplete("Fuel returned an invalid model entry")
                owner, name = row.get("owner"), row.get("name")
                if not all(isinstance(value, str) and value.strip() for value in (owner, name)):
                    raise AssetSearchIncomplete("Fuel model is missing its owner/name")
                identity = (owner, name)
                if identity in seen:
                    continue
                seen.add(identity)
                if len(candidates) >= max_candidates:
                    raise AssetSearchIncomplete("Fuel lookup exceeded its candidate limit; narrow the query")
                url = FUEL_API + "/" + quote(owner, safe="") + "/models/" + quote(name, safe="")
                item, _ = fetch(url)
                if not isinstance(item, dict) or item.get("owner") != owner or item.get("name") != name:
                    raise AssetSearchIncomplete("Fuel model metadata does not match the search result")
                version = item.get("version")
                if isinstance(version, bool) or not str(version).isdigit() or int(version) < 1:
                    raise AssetSearchIncomplete("Fuel model has no pinned version")
                license_name, license_url = item.get("license_name"), item.get("license_url")
                if not all(isinstance(value, str) and value.strip() for value in (license_name, license_url)):
                    raise AssetSearchIncomplete("Fuel model has no license evidence")
                revision = str(version)
                candidates.append(AssetCandidate(
                    id=f"fuel:{owner}/{name}", source_url=url + "/" + revision,
                    revision=revision, license=license_name,
                    metadata={"name": name, "owner": owner, "description": item.get("description", ""),
                              "license_url": license_url, "metadata_url": url,
                              "download_url": url + "/" + revision + "/" + quote(name, safe="") + ".zip",
                              "compatibility": "not_checked"},
                ))
            # Fuel can return short pages while advertising more results. Follow
            # pagination presence, never arbitrary server-provided next URLs.
            if "next" not in links:
                return AssetSearch(query, (FUEL_API,), tuple(candidates), True,
                                   {"requests": requests, "queried_at": datetime.now(timezone.utc).isoformat(),
                                    "scope": "Gazebo Fuel catalog; compatibility not evaluated"})
        raise AssetSearchIncomplete("Fuel lookup exceeded its page limit; narrow the query")
    except (httpx.HTTPError, ValueError) as error:
        raise AssetSearchIncomplete("Fuel lookup failed; asset creation is not authorized by a failed search") from error
