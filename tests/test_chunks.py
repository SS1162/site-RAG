import pytest
from sqlalchemy.exc import IntegrityError

from siterag.adapters.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from siterag.domain.entities import Chunk
from siterag.domain.hashing import make_chunk_id
from tests.conftest import UowFactory, discover, start_run


def build_chunks(url_id: str, texts: list[str], heading: list[str] | None = None) -> list[Chunk]:
    heading = heading if heading is not None else ["תביעות", "נזקי מים"]
    return [
        Chunk(
            chunk_id=make_chunk_id(url_id, heading, i),
            url_id=url_id,
            ordinal=i,
            heading_path=heading,
            text=text,
            token_count=len(text.split()),
            chunker_version="1",
        )
        for i, text in enumerate(texts)
    ]


def setup_page(uow: SqlAlchemyUnitOfWork) -> str:
    run = start_run(uow)
    return discover(uow, "/a", run.run_id).url_id


def test_replace_and_list(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        url_id = setup_page(uow)
        uow.chunks.replace_for_page(url_id, build_chunks(url_id, ["א", "ב", "ג"]))
        uow.commit()
    with make_uow() as uow:
        stored = uow.chunks.list_by_page(url_id)
        assert [c.text for c in stored] == ["א", "ב", "ג"]
        assert stored[0].heading_path == ["תביעות", "נזקי מים"]
        assert stored[0].created_at.tzinfo is not None
        # Re-run with the same content: same ids, no conflicts with the loaded rows.
        uow.chunks.replace_for_page(url_id, build_chunks(url_id, ["א", "ב"]))
        uow.commit()
    with make_uow() as uow:
        assert [c.chunk_id for c in uow.chunks.list_by_page(url_id)] == [
            c.chunk_id for c in build_chunks(url_id, ["x", "y"])
        ]
        assert uow.chunks.count() == 2


def test_chunk_ids_deterministic_across_runs() -> None:
    first = build_chunks("u" * 16, ["a", "b"])
    second = build_chunks("u" * 16, ["a changed", "b changed"])
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


def test_replace_is_atomic(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        url_id = setup_page(uow)
        uow.chunks.replace_for_page(url_id, build_chunks(url_id, ["old1", "old2"]))
        uow.commit()

        bad = build_chunks(url_id, ["new1", "new2"])
        bad[1].chunk_id = bad[0].chunk_id  # duplicate primary key -> insert fails
        with pytest.raises(IntegrityError):
            uow.chunks.replace_for_page(url_id, bad)
        # The savepoint rolled back the delete too; the outer transaction is still usable.
        assert [c.text for c in uow.chunks.list_by_page(url_id)] == ["old1", "old2"]
        uow.commit()
    with make_uow() as uow:
        assert [c.text for c in uow.chunks.list_by_page(url_id)] == ["old1", "old2"]


def test_replace_rejects_foreign_chunks(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        url_id = setup_page(uow)
        with pytest.raises(ValueError):
            uow.chunks.replace_for_page(url_id, build_chunks("z" * 16, ["x"]))


def test_iter_all_batches_and_delete(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        a = discover(uow, "/a", run.run_id).url_id
        b = discover(uow, "/b", run.run_id).url_id
        uow.chunks.replace_for_page(a, build_chunks(a, ["1", "2", "3"]))
        uow.chunks.replace_for_page(b, build_chunks(b, ["4", "5"]))
        assert sorted(c.text for c in uow.chunks.iter_all(batch_size=2)) == ["1", "2", "3", "4", "5"]
        assert uow.chunks.delete_for_page(a) == 3
        assert uow.chunks.count() == 2
