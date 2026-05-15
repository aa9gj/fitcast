"""Regression tests for release-readiness edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline


def _job(i: int) -> dict:
    return {
        "source": "greenhouse",
        "id": str(i),
        "title": f"Data Scientist {i}",
        "company": "example",
        "location": "Remote",
        "url": f"https://example.com/jobs/{i}",
        "content_html": "<p>Python data modeling regulatory analytics role.</p>",
        "posted_at": None,
    }


class TestEnabledSources:
    def test_greenhouse_only(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
        assert pipeline.enabled_sources({"greenhouse": {"companies": ["acme"]}}) == ["greenhouse"]

    def test_lever_only(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
        assert pipeline.enabled_sources({"lever": {"companies": ["mistral"]}}) == ["lever"]

    def test_ashby_only(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
        assert pipeline.enabled_sources({"ashby": {"companies": ["linear"]}}) == ["ashby"]

    def test_muse_only(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
        assert pipeline.enabled_sources({"muse": {"categories": ["Data and Analytics"]}}) == ["muse"]

    def test_bootstrap_greenhouse_counts_as_source(self, monkeypatch, tmp_path):
        bootstrap = tmp_path / "companies.bootstrap.yaml"
        bootstrap.write_text("greenhouse_companies:\n  - acme\n")
        monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", bootstrap)
        assert pipeline.enabled_sources({}) == ["greenhouse"]

    def test_no_sources(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
        assert pipeline.enabled_sources({}) == []


def test_scrape_jobs_does_not_mark_unselected_jobs_seen(monkeypatch, tmp_path):
    """Only successfully analyzed jobs should enter seen.json, not the whole pool."""
    monkeypatch.setattr(pipeline, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(pipeline, "APPLIED_PATH", tmp_path / "applied.json")
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(pipeline, "fetch_greenhouse_jobs", lambda slug: [_job(i) for i in range(5)])

    config = {
        "greenhouse": {"companies": ["example"]},
        "keywords": [],
        "max_jobs": 1,
        "prerank": {"enabled": False},
    }

    selected = pipeline.scrape_jobs(config, client=object(), resume="resume")

    assert len(selected) == 1
    assert not pipeline.SEEN_PATH.exists()


def test_main_fails_if_all_selected_jobs_fail_analysis(monkeypatch, tmp_path):
    resume_path = tmp_path / "resume.md"
    config_path = tmp_path / "config.yaml"
    results_csv = tmp_path / "results.csv"
    results_json = tmp_path / "results.json"
    resume_path.write_text("# Jane Doe\n\nPython data scientist.")
    config_path.write_text("greenhouse:\n  companies:\n    - example\n")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["pipeline.py"])
    monkeypatch.setattr(pipeline, "RESUME_PATH", resume_path)
    monkeypatch.setattr(pipeline, "CONFIG_PATH", config_path)
    monkeypatch.setattr(pipeline, "RESULTS_CSV", results_csv)
    monkeypatch.setattr(pipeline, "RESULTS_JSON", results_json)
    monkeypatch.setattr(pipeline, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(pipeline, "APPLIED_PATH", tmp_path / "applied.json")
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(pipeline, "scrape_jobs", lambda *args, **kwargs: [_job(1)])
    monkeypatch.setattr(pipeline, "analyze_job", lambda *args, **kwargs: (None, "posting text"))
    monkeypatch.setattr(pipeline, "get_embedding_model", lambda: None)
    monkeypatch.setattr(pipeline.anthropic, "Anthropic", lambda *a, **kw: object())

    with pytest.raises(SystemExit) as exc:
        pipeline.main()

    assert "No jobs could be analyzed successfully" in str(exc.value)
    assert not results_csv.exists()
    assert not results_json.exists()
    assert not pipeline.SEEN_PATH.exists()
