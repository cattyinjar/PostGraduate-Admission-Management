from __future__ import annotations

from urllib.request import urlopen

import pytest

from pgam.testing.platform import LocalAdmissionPlatform
from pgam.testing.verify_local_monitor import verify


def test_local_platform_serves_three_real_local_sites():
    with LocalAdmissionPlatform() as platform:
        assert len({site.port for site in platform.sites}) == 3
        for site in platform.sites:
            assert site.list_url.startswith("http://127.0.0.1:")
            with urlopen(site.list_url, timeout=5) as response:
                assert response.status == 200

        with urlopen(platform.site_b.list_url, timeout=5) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = response.read()

        assert "text/html" in content_type and "charset=gbk" in content_type
        document = raw.decode("gbk")
        assert "Site B" in document
        assert platform.site_b.announcements[0].title in document

        platform.site_a.publish("Site A isolated publication", "Isolated body")
        with urlopen(platform.site_b.list_url, timeout=5) as response:
            assert b"Site A isolated publication" not in response.read()
        with urlopen(platform.site_c.list_url, timeout=5) as response:
            assert b"Site A isolated publication" not in response.read()


@pytest.fixture(scope="module")
def local_verification_result():
    return verify()


def test_local_platform_baseline_for_multiple_sites(local_verification_result):
    result = local_verification_result
    assert result["all_runs_successful"]
    assert len(result["sites"]) == 3
    assert all(site["baseline_items"] == 3 for site in result["sites"])
    assert result["baseline_event_count"] == 0
    assert all(site["snapshot_contains_published_title"] for site in result["sites"])


def test_decorative_changes_do_not_create_events(local_verification_result):
    result = local_verification_result
    assert result["baseline_event_count"] == 0
    assert result["decorative_event_count"] == 0
    assert result["decorative_changes_created_events"] is False
    assert result["event_count"] == 3


def test_published_items_are_detected_and_detail_fetched(local_verification_result):
    result = local_verification_result
    assert {site["name"] for site in result["sites"]} == {
        "site_a_static_ul",
        "site_b_table_gbk",
        "site_c_rss",
    }
    for site in result["sites"]:
        assert site["detected"] is True
        assert site["detail_fetched"] is True
        assert site["detail_contains"] is True
        assert site["status"] == "fetched"
        assert site["snapshot_contains_published_title"] is True

    site_b = next(site for site in result["sites"] if site["name"] == "site_b_table_gbk")
    assert site_b["attachment_extracted"] is True


def test_llm_and_email_are_not_invoked(local_verification_result):
    result = local_verification_result
    assert result["summary_records"] == 0
    assert result["notification_records"] == 0
    assert all(site["status"] == "fetched" for site in result["sites"])
    assert result["all_passed"] is True
