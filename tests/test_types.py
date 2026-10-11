from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import StatementError

from siterag.adapters.sqlalchemy.types import UTCDateTime
from tests.conftest import FakeClock, UowFactory, start_run


def test_utc_datetime_round_trips_as_aware(make_uow: UowFactory, clock: FakeClock) -> None:
    clock.now = datetime(2026, 10, 11, 12, 30, 15, 123456, tzinfo=timezone(timedelta(hours=3)))
    with make_uow() as uow:
        run = start_run(uow)
        uow.commit()
    with make_uow() as uow:
        stored = uow.runs.get(run.run_id)
    assert stored is not None
    assert stored.started_at.tzinfo is not None
    assert stored.started_at.utcoffset() == timedelta(0)
    assert stored.started_at == clock.now  # same instant, normalized to UTC


def test_stored_as_fixed_width_utc_string(make_uow: UowFactory, engine: Engine) -> None:
    with make_uow() as uow:
        run = start_run(uow)
        uow.commit()
    with engine.connect() as conn:
        raw = conn.execute(
            text("SELECT started_at FROM runs WHERE run_id = :id"), {"id": run.run_id}
        ).scalar_one()
    assert raw == "2026-10-11T10:00:00.000000+00:00"


def test_naive_datetime_rejected(make_uow: UowFactory, clock: FakeClock) -> None:
    clock.now = datetime(2026, 10, 11, 10, 0)
    with make_uow() as uow, pytest.raises(StatementError, match="Naive datetime"):
        start_run(uow)


def test_result_without_offset_is_treated_as_utc() -> None:
    value = UTCDateTime().process_result_value("2026-10-11T10:00:00", None)  # type: ignore[arg-type]
    assert value is not None and value.utcoffset() == timedelta(0)
