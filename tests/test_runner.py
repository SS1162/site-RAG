import pytest

from siterag.domain.errors import ConfigError
from siterag.pipeline.runner import select_stages

STAGES = ["discover", "fetch", "extract"]


def test_select_stages() -> None:
    assert select_stages(STAGES) == STAGES
    assert select_stages(STAGES, only="fetch") == ["fetch"]
    assert select_stages(STAGES, from_="fetch") == ["fetch", "extract"]
    with pytest.raises(ConfigError):
        select_stages(STAGES, only="nope")
