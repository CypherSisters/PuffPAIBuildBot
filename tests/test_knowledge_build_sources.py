"""The build's knowledge sources, reconciled against the disk.

Covers the four statuses a source or pack can hold, and the two failures that
made an owner's space bot show "0 packs, nothing indexed" after the wizard said
it had fetched things: a fetched artifact that nothing lists, and chosen packs
that were never downloaded and never said so.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import json
import os

import pytest

from puffpai.knowledge import build_sources as bs


REGISTRY = {
    "schema": "source_registry/v1",
    "sources": [
        {
            "id": "nasa_open_apis",
            "label": "NASA Open APIs",
            "kind": "docs",
            "domain": "astronomy",
            "license": "PublicDomain-USGov",
            "commercial_use": True,
            "source_url": "https://api.nasa.gov/",
            "filename": "nasa_open_apis.html",
            "size_mb": 2,
            "what_it_is": "NASA's own catalog of public data feeds.",
        },
        {
            "id": "kiwix_astronomy_stackexchange",
            "label": "Astronomy Stack Exchange",
            "kind": "zim",
            "domain": "astronomy",
            "license": "CC-BY-SA-4.0",
            "commercial_use": True,
            "source_url": "https://download.kiwix.org/zim/x/astronomy.zim",
            "filename": "astronomy.stackexchange.com_en_all.zim",
            "size_mb": 900,
        },
        {
            "id": "kiwix_space_stackexchange",
            "label": "Space Exploration Stack Exchange",
            "kind": "zim",
            "domain": "astronomy",
            "license": "CC-BY-SA-4.0",
            "commercial_use": True,
            "source_url": "https://download.kiwix.org/zim/x/space.zim",
            "filename": "space.stackexchange.com_en_all.zim",
            "size_mb": 700,
        },
        {
            "id": "kiwix_wikipedia_en_nopic",
            "label": "Wikipedia (no images)",
            "kind": "zim",
            "domain": "astronomy",
            "license": "CC-BY-SA-4.0",
            "commercial_use": True,
            "source_url": "https://download.kiwix.org/zim/wikipedia/x.zim",
            "filename": "wikipedia_en_all_nopic.zim",
            "size_mb": 57000,
        },
        {
            "id": "kiwix_ifixit",
            "label": "iFixit repair guides",
            "kind": "zim",
            "domain": "home",
            "license": "CC-BY-NC-SA-3.0",
            "commercial_use": False,
            "source_url": "https://download.kiwix.org/zim/ifixit/ifixit.zim",
            "filename": "ifixit_en_all.zim",
            "size_mb": 4200,
        },
    ],
}


def _manifest():
    """The owner's space-bot manifest, as the wizard wrote it."""
    return {
        "knowledge": {
            "zim_dir": None,
            "auto_index": True,
            "packs": ["pack:wikipedia", "pack:stackexchange_science"],
            "sources": {
                "offered": [
                    "kiwix_astronomy_stackexchange",
                    "kiwix_space_stackexchange",
                    "nasa_open_apis",
                    "kiwix_ifixit",
                ],
                "fetched": [
                    {"id": "nasa_open_apis", "label": "NASA Open APIs",
                     "license": "PublicDomain-USGov", "bytes": 2048,
                     "sha256": "ab" * 32},
                ],
                "not_fetched": [],
            },
        }
    }


def _write_fetched(directory, entry, *, body=b"<html>hi</html>"):
    """A downloaded artifact plus the provenance sidecar source_fetch writes."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, entry["filename"])
    with open(path, "wb") as handle:
        handle.write(body)
    record = {
        "schema": "source_provenance/v1",
        "source_id": entry["id"],
        "label": entry["label"],
        "kind": entry["kind"],
        "license": entry["license"],
        "source_url": entry["source_url"],
        "filename": entry["filename"],
        "path": path,
        "bytes": len(body),
        "sha256": "cd" * 32,
        "fetched_at": "2026-09-19T00:00:00+00:00",
        "revoked_at": None,
    }
    with open(path + ".provenance.json", "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    return path


@pytest.fixture()
def dirs(tmp_path):
    data = tmp_path / "data"
    zim = tmp_path / "zim"
    (data / "sources").mkdir(parents=True)
    zim.mkdir()
    return str(data), str(zim)


def _status(manifest, dirs_, **kw):
    data, zim = dirs_
    return bs.source_status(manifest, registry=REGISTRY, data_dir=data,
                            zim_dir=zim, pack_records=kw.pop("pack_records", []),
                            **kw)


def _item(result, source_id):
    for item in result["items"]:
        if item["id"] == source_id:
            return item
    raise AssertionError(f"{source_id} not in {[i['id'] for i in result['items']]}")


# ── the four statuses ──────────────────────────────────────────────────────

def test_offered_but_not_taken_is_a_suggestion(dirs):
    result = _status(_manifest(), dirs)
    item = _item(result, "kiwix_ifixit")
    assert item["status"] == bs.STATUS_OFFERED
    assert item["selected_by"] == []


def test_chosen_pack_that_never_downloaded_says_size_and_command(dirs):
    result = _status(_manifest(), dirs)

    wiki = _item(result, "kiwix_wikipedia_en_nopic")
    assert wiki["status"] == bs.STATUS_SELECTED_NOT_DOWNLOADED
    assert wiki["selected_by"] == ["pack:wikipedia"]
    assert wiki["size_label"] == "about 55.7 GB"
    assert wiki["command"] == (
        "puffpai sources add kiwix_wikipedia_en_nopic")

    # One interview pack, two registry rows, and the owner is told about both.
    space = _item(result, "kiwix_space_stackexchange")
    assert space["status"] == bs.STATUS_SELECTED_NOT_DOWNLOADED
    assert space["selected_by"] == ["pack:stackexchange_science"]


def test_unknown_size_reads_as_large_download(dirs):
    registry = json.loads(json.dumps(REGISTRY))
    for row in registry["sources"]:
        if row["id"] == "kiwix_wikipedia_en_nopic":
            row["size_mb"] = None
    data, zim = dirs
    result = bs.source_status(_manifest(), registry=registry, data_dir=data,
                              zim_dir=zim, pack_records=[])
    assert _item(result, "kiwix_wikipedia_en_nopic")["size_label"] == "large download"


def test_fetched_docs_row_is_a_live_source_not_fake_indexing(dirs):
    data, zim = dirs
    _write_fetched(os.path.join(data, "sources"), REGISTRY["sources"][0])

    result = _status(_manifest(), dirs)
    item = _item(result, "nasa_open_apis")
    assert item["status"] == bs.STATUS_FETCHED_NOT_INDEXED
    assert item["form"] == bs.FORM_LIVE_SOURCE
    assert item["source_url"] == "https://api.nasa.gov/"
    assert item["what_it_is"]
    assert item["filename"] == "nasa_open_apis.html"
    # Never dressed up as a shelf of documents, and never handed a sync command
    # that could not help it.
    assert item["command"] == ""
    assert result["counts"][bs.STATUS_INDEXED] == 0


def test_fetched_pack_waiting_on_the_indexer(dirs):
    data, zim = dirs
    entry = REGISTRY["sources"][1]
    _write_fetched(zim, entry)

    result = _status(_manifest(), dirs)
    item = _item(result, "kiwix_astronomy_stackexchange")
    assert item["status"] == bs.STATUS_FETCHED_NOT_INDEXED
    assert item["form"] == bs.FORM_DOCUMENTS
    assert item["command"] == "puffpai knowledge sync"


def test_fetched_pack_that_the_indexer_finished_is_indexed(dirs):
    data, zim = dirs
    entry = REGISTRY["sources"][1]
    _write_fetched(zim, entry)
    records = [{
        "status": "complete",
        "filename": entry["filename"],
        "path": os.path.join(zim, entry["filename"]),
        "source_id": entry["id"],
        "docs": 12, "chunks": 340,
    }]

    result = _status(_manifest(), dirs, pack_records=records)
    item = _item(result, "kiwix_astronomy_stackexchange")
    assert item["status"] == bs.STATUS_INDEXED
    assert result["counts"][bs.STATUS_INDEXED] == 1


def test_all_four_statuses_in_one_build(dirs):
    data, zim = dirs
    _write_fetched(os.path.join(data, "sources"), REGISTRY["sources"][0])
    _write_fetched(zim, REGISTRY["sources"][1])
    records = [{"status": "complete", "filename": REGISTRY["sources"][1]["filename"],
                "source_id": REGISTRY["sources"][1]["id"], "docs": 1, "chunks": 2}]

    result = _status(_manifest(), dirs, pack_records=records)
    by_status = {i["id"]: i["status"] for i in result["items"]}
    assert by_status["kiwix_astronomy_stackexchange"] == bs.STATUS_INDEXED
    assert by_status["nasa_open_apis"] == bs.STATUS_FETCHED_NOT_INDEXED
    assert by_status["kiwix_wikipedia_en_nopic"] == bs.STATUS_SELECTED_NOT_DOWNLOADED
    assert by_status["kiwix_ifixit"] == bs.STATUS_OFFERED
    assert sum(result["counts"].values()) == len(result["items"])


# ── listing: what the dashboard could not see before ───────────────────────

def test_an_indexed_pack_is_listed_even_after_the_file_is_deleted(dirs):
    """Chunks in the store outlive the file; the status follows the store."""
    data, zim = dirs
    entry = REGISTRY["sources"][1]
    records = [{"status": "complete", "filename": entry["filename"],
                "source_id": entry["id"], "docs": 3, "chunks": 9}]
    manifest = _manifest()
    manifest["knowledge"]["sources"]["fetched"].append({"id": entry["id"]})

    result = _status(manifest, dirs, pack_records=records)
    assert _item(result, entry["id"])["status"] == bs.STATUS_INDEXED


def test_manifest_says_fetched_but_nothing_is_on_this_disk(dirs):
    result = _status(_manifest(), dirs)
    item = _item(result, "nasa_open_apis")
    assert item["status"] == bs.STATUS_FETCHED_NOT_INDEXED
    assert "not in the knowledge folders" in item["note"]


def test_a_source_on_disk_with_no_manifest_row_is_still_listed(dirs):
    """Provenance on disk is enough; a manifest is not the only witness."""
    data, zim = dirs
    _write_fetched(zim, REGISTRY["sources"][4])
    result = _status({"knowledge": {}}, dirs)
    assert _item(result, "kiwix_ifixit")["status"] == bs.STATUS_FETCHED_NOT_INDEXED


def test_a_sidecar_whose_artifact_is_gone_is_not_a_download(dirs):
    data, zim = dirs
    path = _write_fetched(zim, REGISTRY["sources"][4])
    os.remove(path)
    assert bs.on_disk_sources(zim) == {}


def test_a_chosen_pack_with_no_confirmed_download_says_so(dirs):
    manifest = _manifest()
    manifest["knowledge"]["packs"] = ["pack:wikem"]
    result = _status(manifest, dirs)
    item = _item(result, "pack:wikem")
    assert item["status"] == bs.STATUS_SELECTED_NOT_DOWNLOADED
    assert item["command"] == "puffpai knowledge sync"
    assert "cannot download this one" in item["note"]


def test_a_chosen_pack_the_library_has_points_at_the_book_and_the_add_command(dirs):
    manifest = _manifest()
    manifest["knowledge"]["packs"] = ["pack:knots"]
    result = _status(manifest, dirs)
    item = _item(result, "kiwix:wikipedia_en_knots:maxi")
    assert item["status"] == bs.STATUS_SELECTED_NOT_DOWNLOADED
    assert item["selected_by"] == ["pack:knots"]
    assert item["command"] == "puffpai sources add kiwix:wikipedia_en_knots:maxi"
    assert item["label"].startswith("Knots by Wikipedia")


def test_no_manifest_is_an_answer_not_an_error(dirs):
    result = _status(None, dirs)
    assert result["schema"] == bs.SCHEMA
    assert result["items"] == []


def test_the_shipped_registry_backs_every_mapped_pack():
    """A pack may not point at a list row that does not exist."""
    from puffpai import catalogs, source_registry
    ids = {str(r.get("id")) for r in source_registry.load_registry().get("sources", [])}
    packs = (catalogs.catalog_row("kiwix") or {}).get("interview_packs") or {}
    for pack, mapped in packs.items():
        if not pack.startswith("pack:"):
            continue
        for source_id in mapped:
            assert source_id.startswith("kiwix:") or source_id in ids,                 f"{pack} -> missing row {source_id}"


def test_the_pack_mapping_is_data_not_code():
    """No topic table in code: the mapping lives in config/catalog_index.json."""
    assert not hasattr(bs, "PACK_SOURCE_IDS")
    assert bs.pack_source_ids("pack:wikipedia") == ("kiwix_wikipedia_en_nopic",)


def test_a_mapped_id_that_is_nowhere_is_dropped(monkeypatch):
    from puffpai import catalogs
    fake = {"interview_packs": {"pack:x": ["not_a_row", "kiwix:real_book", "kiwix_space_stackexchange"]}}
    monkeypatch.setattr(catalogs, "catalog_row", lambda _id: fake)
    assert bs.pack_source_ids("pack:x") == ("kiwix:real_book", "kiwix_space_stackexchange")
    fake["interview_packs"]["pack:x"] = "kiwix:not_a_list"
    assert bs.pack_source_ids("pack:x") == ()


def test_every_catalog_mapped_pack_is_a_downloadable_book():
    from puffpai import catalogs
    from puffpai.catalogs import knowledge_resolver
    packs = (catalogs.catalog_row("kiwix") or {}).get("interview_packs") or {}
    real = {k: v for k, v in packs.items() if k.startswith("pack:")}
    assert real, "the mapping is empty"
    for pack, ids in real.items():
        assert bs.pack_source_ids(pack) == tuple(ids)
        for ident in ids:
            cand = knowledge_resolver.candidate_by_id(ident)
            assert cand and cand["fetchable"], f"{pack} -> {ident}"


def test_a_pack_with_no_confirmed_download_stays_unmapped():
    assert bs.pack_source_ids("pack:wikem") == ()
    assert bs.pack_source_ids("pack:not_a_pack") == ()


def test_shipped_zim_rows_all_record_a_size():
    """A large download the owner is not warned about is the whole bug."""
    from puffpai import source_registry
    for row in source_registry.load_registry().get("sources", []):
        if row.get("kind") == "zim":
            assert row.get("size_mb"), f"{row.get('id')} has no size_mb"
