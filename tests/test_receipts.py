from dataclasses import replace

import pytest

from siterag.adapters.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from siterag.domain.entities import Page
from siterag.domain.enums import PageStatus
from tests.conftest import UowFactory, discover, start_run


def needing(uow: SqlAlchemyUnitOfWork, stage: str = "extract", version: str = "1") -> list[str]:
    return [p.url_id for p in uow.pages.iter_needing_stage(stage, version)]


def fetched(uow: SqlAlchemyUnitOfWork, path: str, run_id: str, raw_hash: str = "h1") -> Page:
    page = replace(discover(uow, path, run_id), raw_hash=raw_hash, status=PageStatus.FETCHED)
    uow.pages.update(page)
    return page


def test_selected_without_receipt_and_not_without_input(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = fetched(uow, "/a", run.run_id)
        discover(uow, "/not-fetched", run.run_id)  # raw_hash is NULL -> not ready
        assert needing(uow) == [page.url_id]


def test_receipt_lifecycle(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = fetched(uow, "/a", run.run_id)
        uow.receipts.upsert(page.url_id, "extract", "h1", "1", run.run_id)
        assert needing(uow) == []  # up to date

        uow.pages.update(replace(page, raw_hash="h2"))
        assert needing(uow) == [page.url_id]  # input hash changed

        uow.receipts.upsert(page.url_id, "extract", "h2", "1", run.run_id)
        assert needing(uow) == []
        assert needing(uow, version="2") == [page.url_id]  # stage version bumped

        receipt = uow.receipts.get(page.url_id, "extract")
        assert receipt is not None and receipt.input_hash == "h2"
        assert uow.receipts.get(page.url_id, "chunk") is None


@pytest.mark.parametrize("mark", ["removed", "redirected", "duplicate", "failed_permanent"])
def test_inactive_pages_never_selected(make_uow: UowFactory, mark: str) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        other = fetched(uow, "/other", run.run_id)
        page = fetched(uow, "/a", run.run_id)
        if mark == "removed":
            uow.pages.mark_removed(page.url_id, run.run_id)
        elif mark == "redirected":
            uow.pages.mark_redirected(page.url_id, other.url_id, run.run_id)
        elif mark == "duplicate":
            uow.pages.mark_duplicate(page.url_id, other.url_id, run.run_id)
        else:
            uow.pages.update(replace(page, status=PageStatus.FAILED_PERMANENT))
        assert needing(uow) == [other.url_id]
        assert needing(uow, version="99") == [other.url_id]


def test_stage_input_column_comes_from_config(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = fetched(uow, "/a", run.run_id)
        assert needing(uow, "chunk") == []  # clean_hash still NULL
        uow.pages.update(replace(page, clean_hash="c1"))
        assert needing(uow, "chunk") == [page.url_id]
        with pytest.raises(ValueError, match="no input column"):
            needing(uow, "unknown-stage")


def test_limit(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        for i in range(5):
            fetched(uow, f"/p{i}", run.run_id)
        assert len(list(uow.pages.iter_needing_stage("extract", "1", limit=2))) == 2


def test_list_pending_unindex(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = fetched(uow, "/a", run.run_id)
        live = fetched(uow, "/live", run.run_id)
        for p in (page, live):
            uow.receipts.upsert(p.url_id, "index", "ch", "bge-m3:1", run.run_id)
        assert uow.pages.list_pending_unindex() == []

        uow.pages.mark_removed(page.url_id, run.run_id)
        assert [p.url_id for p in uow.pages.list_pending_unindex()] == [page.url_id]

        assert uow.receipts.delete_for_page(page.url_id, "index") == 1
        assert uow.pages.list_pending_unindex() == []


def test_delete_all_receipts_of_page(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        page = fetched(uow, "/a", run.run_id)
        for stage in ("extract", "chunk", "index"):
            uow.receipts.upsert(page.url_id, stage, "h", "1", run.run_id)
        assert uow.receipts.delete_for_page(page.url_id) == 3
        assert uow.receipts.get(page.url_id, "extract") is None
