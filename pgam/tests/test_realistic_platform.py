from __future__ import annotations

from urllib.request import urlopen

import pytest

from pgam.testing.platform import LocalAdmissionPlatform  # noqa: F401
from pgam.testing.realistic_platform import RealisticAdmissionPlatform
from pgam.testing.verify_realistic_monitor import verify


def test_realistic_platform_serves_all_supplied_site_families():
    with RealisticAdmissionPlatform() as platform:
        assert len(platform.sites) == 16
        assert len({site.port for site in platform.sites}) == 16
        expected_names = {
            "ia_cas",
            "tsinghua_yzbm",
            "tsinghua_life",
            "tsinghua_au",
            "sjtu",
            "nju",
            "fudan",
            "ustc",
            "amss",
            "ict",
            "is_cas",
            "ipe",
            "sia",
            "iphy",
            "ime",
            "semi",
        }
        assert {site.name for site in platform.sites} == expected_names

        for site in platform.sites:
            with urlopen(site.list_url, timeout=5) as response:
                assert response.status == 200
                assert len(response.read()) > 0

        with urlopen(next(s for s in platform.sites if s.name == "ipe").list_url) as response:
            assert "charset=gbk" in response.headers.get("Content-Type", "").lower()

        yzbm = next(s for s in platform.sites if s.name == "tsinghua_yzbm")
        with urlopen(yzbm.list_url) as response:
            document = response.read().decode("utf-8")
        assert 'href="javascript:void(0);"' in document
        assert "data-val=" in document


@pytest.fixture(scope="module")
def realistic_verification_result():
    return verify()


def test_realistic_sites_create_independent_baselines(realistic_verification_result):
    result = realistic_verification_result
    assert result["site_count"] == 16
    assert result["all_runs_successful"] is True
    assert result["baseline_event_count"] == 0
    assert result["decorative_event_count"] == 0
    assert result["decorative_changes_created_events"] is False
    assert {site["name"]: site["baseline_items"] for site in result["sites"]} == {
        **{name: 3 for name in {site["name"] for site in result["sites"]} if name != "iphy"},
        "iphy": 1,
    }


def test_all_realistic_sites_detect_publish_and_fetch_details(realistic_verification_result):
    result = realistic_verification_result
    assert result["event_count"] == 16
    for site in result["sites"]:
        assert site["detected"] is True
        assert site["detail_fetched"] is True
        assert site["detail_contains"] is True
        assert site["status"] == "fetched"
        assert site["snapshot_contains_published_title"] is True


def test_realistic_site_verification_has_no_llm_or_email_side_effects(realistic_verification_result):
    result = realistic_verification_result
    assert result["summary_records"] == 0
    assert result["notification_records"] == 0
    assert result["temporary_data_cleaned"] is True
    assert result["all_passed"] is True
