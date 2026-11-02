import httpx
import pytest

from robot_worlds import AssetSearchIncomplete, search_online_assets, source_asset


def model(name="Bowl", **changes):
    return {"owner": "GoogleResearch", "name": name, "version": 1,
            "license_name": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            **changes}


def test_sdk_search_imports_versioned_candidate_before_creation():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/models"):
            assert request.url.params["q"] == "ceramic bowl"
            assert "search" not in request.url.params
            return httpx.Response(200, json=[model("Bowl / white")])
        assert "Bowl%20%2F%20white" in str(request.url)
        return httpx.Response(200, json=model("Bowl / white"))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = source_asset("ceramic bowl", search=lambda q: search_online_assets(q, client=client),
                              import_asset=lambda c: c.metadata["download_url"],
                              create_asset=lambda *args: pytest.fail("Ignored existing asset"))
    assert len(requests) == 2
    assert result.selected.revision == "1"
    assert result.asset.endswith("/1/Bowl%20%2F%20white.zip")
    assert result.search.evidence["requests"] == [str(r.url) for r in requests]
    assert result.selected.metadata["compatibility"] == "not_checked"


def test_short_pages_still_follow_pagination_on_the_fixed_catalog_host():
    def handle(request):
        assert request.url.host == "fuel.gazebosim.org"
        if not request.url.path.endswith("/models"):
            return httpx.Response(200, json=model(request.url.path.split("/")[-1]))
        page = request.url.params["page"]
        return httpx.Response(200, json=[model("Bowl" if page == "1" else "Cup")],
                              headers={"Link": '<https://untrusted.example/>; rel="next"'} if page == "1" else {})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = search_online_assets("container", client=client)
    assert len(result.candidates) == 2
    assert result.completed


@pytest.mark.parametrize("failure", ["http", "json", "shape", "license", "revision", "identity", "pages", "candidates", "size"])
def test_lookup_failures_never_authorize_creation(failure, monkeypatch):
    from robot_worlds import asset_catalog
    if failure == "size":
        monkeypatch.setattr(asset_catalog, "MAX_RESPONSE_BYTES", 8)

    def handle(request):
        if failure == "http":
            return httpx.Response(503)
        if failure == "json":
            return httpx.Response(200, text="not JSON")
        if failure == "shape":
            return httpx.Response(200, json={"models": []})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=[model(), model("Cup")],
                                  headers={"Link": '</models?page=2>; rel="next"'} if failure == "pages" else {})
        changes = {"license_url": None} if failure == "license" else {"version": "tip"} if failure == "revision" else {}
        name = "different" if failure == "identity" else request.url.path.split("/")[-1]
        return httpx.Response(200, json=model(name, **changes))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client, pytest.raises(AssetSearchIncomplete):
        source_asset("bowl", search=lambda q: search_online_assets(q, client=client, max_pages=1,
                     max_candidates=1 if failure == "candidates" else 30),
                     import_asset=lambda c: pytest.fail("Incomplete result reached importer"),
                     create_asset=lambda *args: pytest.fail("Failed search authorized creation"))


def test_default_coordinator_calls_online_search(monkeypatch):
    from robot_worlds import asset_catalog
    from robot_worlds.asset_sourcing import AssetSearch
    queries = []

    def search(query):
        queries.append(query)
        return AssetSearch(query, ("Fuel",), (), True)
    monkeypatch.setattr(asset_catalog, "search_online_assets", search)
    result = source_asset("custom fixture", import_asset=lambda c: pytest.fail("No matches"),
                          create_asset=lambda *args: "new geometry")
    assert queries == ["custom fixture"]
    assert result.decision == "created_after_search"


def test_empty_catalog_result_records_scope_before_creation():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))) as client:
        result = source_asset("custom fixture", search=lambda q: search_online_assets(q, client=client),
                              import_asset=lambda c: pytest.fail("No matches"),
                              create_asset=lambda q, report, rejected: (report.sources, rejected))
    assert result.search.completed
    assert result.asset == (("https://fuel.gazebosim.org/1.0",), [])
