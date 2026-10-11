import pytest

from siterag.adapters.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from siterag.domain.errors import ConfigError
from tests.conftest import UowFactory, start_run


def test_rolls_back_on_exception(make_uow: UowFactory) -> None:
    with pytest.raises(RuntimeError), make_uow() as uow:
        run = start_run(uow)
        raise RuntimeError("boom")
    with make_uow() as uow:
        assert uow.runs.get(run.run_id) is None


def test_uncommitted_work_is_discarded(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
    with make_uow() as uow:
        assert uow.runs.get(run.run_id) is None


def test_explicit_rollback_and_commit(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        discarded = start_run(uow)
        uow.rollback()
        kept = start_run(uow)
        uow.commit()
    with make_uow() as uow:
        assert uow.runs.get(discarded.run_id) is None
        assert uow.runs.get(kept.run_id) is not None


def test_unknown_stage_input_column_fails_fast(make_uow: UowFactory) -> None:
    with pytest.raises(ConfigError, match="no_such_column"):
        SqlAlchemyUnitOfWork(None, {"extract": "no_such_column"})  # type: ignore[arg-type]
