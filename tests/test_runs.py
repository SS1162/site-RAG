import pytest

from siterag.domain.enums import RunStatus
from siterag.domain.errors import NotFoundError, RunLockedError
from tests.conftest import FakeClock, UowFactory, start_run


def test_second_start_raises_run_locked(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        start_run(uow)
        uow.commit()
    with make_uow() as uow, pytest.raises(RunLockedError):
        start_run(uow)


def test_stale_run_is_aborted_and_new_run_starts(make_uow: UowFactory, clock: FakeClock) -> None:
    with make_uow() as uow:
        old = start_run(uow)
        uow.commit()
    clock.advance(hours=12, seconds=1)
    with make_uow() as uow:
        new = uow.runs.start("test", "run", {}, "cfg", "site", stale_after_hours=12)
        uow.commit()
    with make_uow() as uow:
        aborted = uow.runs.get(old.run_id)
        assert aborted is not None
        assert aborted.status == RunStatus.ABORTED
        assert aborted.abort_reason == "stale"
        assert aborted.finished_at == clock.now
        assert uow.runs.get_last() == uow.runs.get(new.run_id)


def test_finished_run_does_not_lock(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        uow.runs.finish(run.run_id, RunStatus.COMPLETED)
        uow.commit()
    with make_uow() as uow:
        start_run(uow)


def test_start_records_metadata(make_uow: UowFactory, clock: FakeClock) -> None:
    with make_uow() as uow:
        run = uow.runs.start("fnx", "run", {"limit": 5, "שם": "ערך"}, "c1", "s1")
        uow.commit()
    with make_uow() as uow:
        stored = uow.runs.get(run.run_id)
    assert stored is not None
    assert (stored.site_id, stored.command, stored.status) == ("fnx", "run", RunStatus.RUNNING)
    assert stored.args == {"limit": 5, "שם": "ערך"}
    assert (stored.config_hash, stored.site_profile_hash) == ("c1", "s1")
    assert stored.started_at == clock.now and stored.finished_at is None


def test_increment_counters(make_uow: UowFactory) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        uow.runs.increment(run.run_id, pages_new=2, pages_failed=1)
        uow.runs.increment(run.run_id, pages_new=3)
        uow.commit()
    with make_uow() as uow:
        stored = uow.runs.get(run.run_id)
        assert stored is not None
        assert (stored.pages_new, stored.pages_failed, stored.pages_changed) == (5, 1, 0)
        with pytest.raises(ValueError):
            uow.runs.increment(run.run_id, pages_bogus=1)


def test_finish_unknown_run(make_uow: UowFactory) -> None:
    with make_uow() as uow, pytest.raises(NotFoundError):
        uow.runs.finish("missing", RunStatus.FAILED)
