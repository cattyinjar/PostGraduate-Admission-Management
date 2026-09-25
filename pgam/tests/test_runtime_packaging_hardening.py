from __future__ import annotations

import pytest

from pgam.services.services import _validate_url
from pgam.sources.extractor import DetailExtractor


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.edu.cn/list.htm", "https://example.edu.cn/list.htm"),
        (
            "https://ime.cas.cn/kjrh/tzggkjrh/](https://ime.cas.cn/kjrh/tzggkjrh/",
            "https://ime.cas.cn/kjrh/tzggkjrh/",
        ),
        ("[notice](https://example.edu.cn/list.htm)", "https://example.edu.cn/list.htm"),
    ],
)
def test_validate_url_recovers_markdown_copies(raw, expected):
    assert _validate_url(raw) == expected


def test_detail_extractor_falls_back_when_trafilatura_fails(monkeypatch):
    from pgam.sources import extractor

    def fail(*args, **kwargs):
        raise RuntimeError("No option 'min_extracted_size' in section: 'DEFAULT'")

    assert extractor.trafilatura is not None
    monkeypatch.setattr(extractor.trafilatura, "extract", fail)
    html = "<html><body><article><h1>Admission notice</h1><p>DETAIL-MARKER</p></article></body></html>"
    result = DetailExtractor().extract("https://example.edu.cn/detail.htm", html.encode("utf-8"))
    assert "DETAIL-MARKER" in result.text
