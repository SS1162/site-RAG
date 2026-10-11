import pytest

from siterag.domain.errors import DiscoveryTooSmallError, RemovalThresholdExceededError
from siterag.domain.safety import check_discovery, check_removals


def test_check_discovery() -> None:
    check_discovery(50, 50)
    with pytest.raises(DiscoveryTooSmallError):
        check_discovery(49, 50)


def test_check_removals_thresholds() -> None:
    check_removals(15, 100, 0.15)  # exactly at the limit is allowed
    check_removals(0, 0, 0.15)
    with pytest.raises(RemovalThresholdExceededError):
        check_removals(16, 100, 0.15)
