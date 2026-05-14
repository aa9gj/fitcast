"""Tests for bootstrap_companies.py slug extraction and pipeline wiring.

Covers all three ATS patterns (Greenhouse / Lever / Ashby) plus the pipeline
side that consumes the resulting companies.bootstrap.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_companies
import pipeline


# ───────────────────── slug extraction (bootstrap_companies) ─────────────────────

def test_extract_greenhouse_slugs():
    listings = [
        {"url": "https://boards.greenhouse.io/acme/jobs/123"},
        {"url": "https://job-boards.greenhouse.io/beta-co/jobs/456"},
        {"url": "https://boards-api.greenhouse.io/v1/boards/gamma_inc/jobs"},
    ]
    assert bootstrap_companies.extract_greenhouse_slugs(listings) == {"acme", "beta-co", "gamma_inc"}


def test_extract_lever_slugs():
    listings = [
        {"url": "https://jobs.lever.co/mistral/abc-def"},
        {"url": "https://jobs.lever.co/benchling01"},
        {"url": "https://jobs.lever.co/Color/posting/xyz"},
    ]
    assert bootstrap_companies.extract_lever_slugs(listings) == {"mistral", "benchling01", "color"}


def test_extract_ashby_slugs():
    listings = [
        {"url": "https://jobs.ashbyhq.com/linear/role-1"},
        {"url": "https://jobs.ashbyhq.com/ramp"},
        {"url": "https://jobs.ashbyhq.com/Notion/123"},
    ]
    assert bootstrap_companies.extract_ashby_slugs(listings) == {"linear", "ramp", "notion"}


def test_extract_ignores_unrelated_urls():
    listings = [
        {"url": "https://linkedin.com/jobs/view/123"},
        {"url": "https://indeed.com/viewjob?jk=abc"},
        {"url": "https://workday.com/some-company"},
    ]
    assert bootstrap_companies.extract_greenhouse_slugs(listings) == set()
    assert bootstrap_companies.extract_lever_slugs(listings) == set()
    assert bootstrap_companies.extract_ashby_slugs(listings) == set()


def test_extract_handles_missing_or_null_urls():
    listings = [{}, {"url": None}, {"url": ""}, {"url": "https://jobs.lever.co/valid"}]
    assert bootstrap_companies.extract_lever_slugs(listings) == {"valid"}


def test_extract_drops_long_hex_opaque_ids():
    """Real Greenhouse slugs are short; opaque hex IDs of >32 chars should be skipped."""
    listings = [
        {"url": "https://boards.greenhouse.io/" + "a" * 33 + "/jobs/1"},  # would be all-hex
        {"url": "https://boards.greenhouse.io/realco/jobs/1"},
    ]
    # The 33-char "a..a" string is all hex characters and >32 long, so it's dropped.
    assert bootstrap_companies.extract_greenhouse_slugs(listings) == {"realco"}


# ───────────────────── pipeline wiring (load + enabled_sources) ─────────────────────

def test_load_bootstrap_returns_all_three_keys(tmp_path, monkeypatch):
    bootstrap_file = tmp_path / "companies.bootstrap.yaml"
    bootstrap_file.write_text(yaml.dump({
        "greenhouse_companies": ["gh-1", "gh-2"],
        "lever_companies": ["lv-1"],
        "ashby_companies": ["as-1", "as-2", "as-3"],
    }))
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", bootstrap_file)
    out = pipeline.load_bootstrap_companies()
    assert out["greenhouse"] == ["gh-1", "gh-2"]
    assert out["lever"] == ["lv-1"]
    assert out["ashby"] == ["as-1", "as-2", "as-3"]


def test_load_bootstrap_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "no_such_file.yaml")
    out = pipeline.load_bootstrap_companies()
    assert out == {"greenhouse": [], "lever": [], "ashby": []}


def test_load_bootstrap_handles_old_single_key_format(tmp_path, monkeypatch):
    """Files written by an older bootstrap_companies.py (greenhouse-only) still work."""
    bootstrap_file = tmp_path / "companies.bootstrap.yaml"
    bootstrap_file.write_text("greenhouse_companies:\n  - acme\n  - beta\n")
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", bootstrap_file)
    out = pipeline.load_bootstrap_companies()
    assert out["greenhouse"] == ["acme", "beta"]
    assert out["lever"] == []
    assert out["ashby"] == []


def test_enabled_sources_counts_bootstrapped_lever(tmp_path, monkeypatch):
    bootstrap_file = tmp_path / "companies.bootstrap.yaml"
    bootstrap_file.write_text("lever_companies:\n  - mistral\n")
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", bootstrap_file)
    # Config has no lever block — but the bootstrap file does.
    assert "lever" in pipeline.enabled_sources({})


def test_enabled_sources_counts_bootstrapped_ashby(tmp_path, monkeypatch):
    bootstrap_file = tmp_path / "companies.bootstrap.yaml"
    bootstrap_file.write_text("ashby_companies:\n  - linear\n")
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", bootstrap_file)
    assert "ashby" in pipeline.enabled_sources({})


def test_collect_from_sources_merges_bootstrap_slugs(monkeypatch, tmp_path):
    """Configured + bootstrapped slugs both reach the fetcher, deduped."""
    bootstrap_file = tmp_path / "companies.bootstrap.yaml"
    bootstrap_file.write_text(yaml.dump({
        "greenhouse_companies": ["gh-bootstrap"],
        "lever_companies": ["lv-bootstrap"],
        "ashby_companies": ["as-bootstrap"],
    }))
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", bootstrap_file)

    called = {"greenhouse": [], "lever": [], "ashby": []}
    monkeypatch.setattr(pipeline, "fetch_greenhouse_jobs", lambda slug: (called["greenhouse"].append(slug) or []))
    monkeypatch.setattr(pipeline, "fetch_lever_jobs", lambda slug: (called["lever"].append(slug) or []))
    monkeypatch.setattr(pipeline, "fetch_ashby_jobs", lambda slug: (called["ashby"].append(slug) or []))
    monkeypatch.setattr(pipeline, "fetch_muse_jobs", lambda **kw: [])

    config = {
        "greenhouse": {"companies": ["gh-config"]},
        "lever": {"companies": ["lv-config"]},
        "ashby": {"companies": ["as-config"]},
    }
    pipeline._collect_from_sources(config)

    assert set(called["greenhouse"]) == {"gh-config", "gh-bootstrap"}
    assert set(called["lever"]) == {"lv-config", "lv-bootstrap"}
    assert set(called["ashby"]) == {"as-config", "as-bootstrap"}
