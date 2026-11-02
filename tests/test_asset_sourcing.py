import pytest

from robot_worlds.asset_sourcing import (
    AssetCandidate,
    AssetIncompatible,
    AssetSearch,
    AssetSearchIncomplete,
    source_asset,
)


def candidate(id="bowl"):
    return AssetCandidate(id, "https://publisher.example/"+id, "pinned-v1", "CC-BY-4.0")


def test_existing_compatible_asset_is_used_before_any_creation():
    calls = []
    def search(query):
        calls.append("search")
        return AssetSearch(query, ("publisher.example",), (candidate(),), True)
    def importer(asset):
        calls.append("import")
        return {"original_asset": asset.id}
    result = source_asset("ceramic bowl", search=search, import_asset=importer,
                          create_asset=lambda *args: pytest.fail("Existing asset was ignored"))
    assert calls == ["search", "import"]
    assert result.decision == "reuse_or_adapt"
    assert result.selected.id == "bowl"


def test_creation_receives_completed_search_and_each_rejection_reason():
    def importer(asset):
        raise AssetIncompatible("Decorative microwave has no door joint")
    def create(query, report, rejected):
        assert report.completed and report.query == query
        assert rejected[0]["reason"] == "Decorative microwave has no door joint"
        return {"new_asset": "microwave"}
    result = source_asset("openable microwave", search=lambda q: AssetSearch(q, ("online catalog",), (candidate("microwave"),), True),
                          import_asset=importer, create_asset=create)
    assert result.decision == "created_after_search"


@pytest.mark.parametrize("report", [
    AssetSearch("bowl", ("online",), (), False),
    AssetSearch("bowl", (), (), True),
    AssetSearch("different request", ("online",), (), True),
])
def test_incomplete_or_wrong_search_cannot_trigger_creation(report):
    with pytest.raises(AssetSearchIncomplete):
        source_asset("bowl", search=lambda q: report, import_asset=lambda c: None,
                     create_asset=lambda *args: pytest.fail("Search failure was treated as no match"))


def test_download_failure_is_not_incompatibility_and_does_not_trigger_creation():
    def importer(asset):
        raise OSError("Download failed")
    with pytest.raises(OSError):
        source_asset("bowl", search=lambda q: AssetSearch(q, ("online",), (candidate(),), True),
                     import_asset=importer, create_asset=lambda *args: pytest.fail("Downloaded asset silently replaced"))


def test_completed_empty_lookup_allows_creation_and_retains_search_scope():
    report = AssetSearch("custom fixture", ("Fuel", "NVIDIA Kitchen"), (), True,
                         {"queries": ["custom fixture"], "matches": 0})
    result = source_asset(report.query, search=lambda q: report,
                          import_asset=lambda c: pytest.fail("No candidate to import"),
                          create_asset=lambda *args: {"custom": True})
    assert result.search is report
    assert result.rejected == []
