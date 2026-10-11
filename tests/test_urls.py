from urllib.parse import quote

import pytest

from siterag.domain.urls import UrlNormalizationRules, normalize_url

RULES = UrlNormalizationRules()
HEBREW = "https://www.fnx.co.il/ביטוח-דירה"
ENCODED = "https://www.fnx.co.il/" + quote("ביטוח-דירה")


@pytest.mark.parametrize(
    "raw",
    [
        HEBREW,
        ENCODED,
        ENCODED.lower().replace("https://www.fnx.co.il/", "https://www.fnx.co.il/"),
        HEBREW + "/",
        HEBREW + "?utm_source=facebook",
        HEBREW + "#שאלות",
        ENCODED + "/?utm_source=facebook&fbclid=x#%D7%A9",
        "http://WWW.FNX.CO.IL:443//ביטוח-דירה//",
    ],
)
def test_equivalent_urls_share_url_id(raw: str) -> None:
    expected = normalize_url(HEBREW, RULES)
    result = normalize_url(raw, RULES)
    assert result.url_id == expected.url_id
    assert result.url == expected.url


def test_encoded_and_display_forms() -> None:
    result = normalize_url(HEBREW, RULES)
    assert result.url == ENCODED
    assert result.url_display == HEBREW
    assert len(result.url_id) == 16


def test_no_double_encoding() -> None:
    once = normalize_url(ENCODED, RULES)
    twice = normalize_url(once.url, RULES)
    assert twice.url == once.url
    assert "%25" not in once.url


def test_root_path_is_kept() -> None:
    assert normalize_url("https://www.fnx.co.il", RULES).url == "https://www.fnx.co.il/"
    assert normalize_url("https://www.fnx.co.il/", RULES).url == "https://www.fnx.co.il/"


def test_keep_params_allow_list() -> None:
    rules = UrlNormalizationRules(keep_params=("id",))
    result = normalize_url("https://x.co.il/p?b=2&id=7&utm_source=a", rules)
    assert result.url == "https://x.co.il/p?id=7"


def test_params_sorted_and_tracking_removed() -> None:
    result = normalize_url("https://x.co.il/p?z=1&a=2&gclid=g&utm_medium=m&_ga=1", RULES)
    assert result.url == "https://x.co.il/p?a=2&z=1"


def test_hebrew_query_value_display() -> None:
    result = normalize_url("https://x.co.il/search?q=ביטוח", RULES)
    assert result.url == "https://x.co.il/search?q=" + quote("ביטוח")
    assert result.url_display == "https://x.co.il/search?q=ביטוח"


def test_www_not_merged_by_default() -> None:
    assert (
        normalize_url("https://www.fnx.co.il/a", RULES).url_id
        != normalize_url("https://fnx.co.il/a", RULES).url_id
    )


def test_strip_www() -> None:
    rules = UrlNormalizationRules(strip_www=True)
    assert normalize_url("https://www.fnx.co.il/a", rules).url == "https://fnx.co.il/a"


def test_non_default_port_kept() -> None:
    assert normalize_url("https://x.co.il:8443/a", RULES).url == "https://x.co.il:8443/a"


def test_missing_host_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_url("/relative/path", RULES)
