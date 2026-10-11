"""Scope and classification against the shipped fnx profile."""

from pathlib import Path

import pytest

from siterag.config.loader import load_site_profile
from siterag.domain.classify import classify
from siterag.domain.scope import decoded_path, has_extension, in_scope

ROOT = Path(__file__).resolve().parents[1]
PROFILE = load_site_profile(ROOT / "sites" / "fnx.yaml")
SCOPE = PROFILE.scope.to_rules()
CLASSES = PROFILE.classification.to_rules()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", True),
        ("/car-insurance/comprehensive/", True),
        ("/claims-customer-service/claims-appartment-insurance/pipeline-damage/", True),
        ("/claims-customer-service/תביעות-רכב/", True),
        ("/qa/מה-זה-השתתפות-עצמית/", True),
        ("/gemel/", True),
        ("/en/about/", False),
        ("/investor-relations/reports/", False),
        ("/gemel/tzgemel/211/", False),
        ("/gemel/tzgemel/", False),
        ("/travel-insurance/abroad-old/", False),
        ("/career/jobs/", False),
        ("/savings/takanon-2024.pdf", False),
        ("/sitemap/", False),
    ],
)
def test_scope(path: str, expected: bool) -> None:
    ok, reason = in_scope(path, SCOPE)
    assert ok is expected
    assert (reason is None) is expected


def test_scope_reason_is_the_matching_rule() -> None:
    assert in_scope("/en/x/", SCOPE) == (False, "^/en/")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/ishtalmutfund/faq/", ("faq", "pension_savings")),
        ("/customer-service/terms-of-use/privacy-policy/", ("legal", "general")),
        ("/customer-service/contact-us/", ("service", "general")),
        ("/claims-customer-service/claims-appartment-insurance/pipeline-damage/",
         ("claims", "apartment")),
        ("/car-insurance/comprehensive/", ("product", "car")),
        ("/travel-insurance/plan-your-trip/", ("article", "travel")),
        ("/health-insurance/dental_information/", ("article", "health")),
        ("/files/policy-car.pdf", ("document", "general")),
        ("/mortgage-insurance/", ("product", "mortgage")),
        ("/pets-insurance/", ("product", "pets")),
        ("/loans/", ("product", "loans")),
        ("/about/", ("general", "general")),
    ],
)
def test_classify(path: str, expected: tuple[str, str]) -> None:
    assert classify(path, CLASSES) == expected


def test_decoded_path_and_extension() -> None:
    url = "https://www.fnx.co.il/%D7%A8%D7%9B%D7%91/Guide.PDF"
    assert decoded_path(url) == "/רכב/Guide.PDF"
    assert decoded_path("https://www.fnx.co.il") == "/"
    assert has_extension("/a/photo.JPG", [".jpg"])
    assert not has_extension("/a/jpg/", [".jpg"])


def test_user_agent_and_placeholders() -> None:
    assert PROFILE.http.user_agent.startswith("site-rag-hackathon/0.1 (+")
    assert set(PROFILE.http.placeholder_fields()) <= {"contact_url", "contact_email"}
