"""Tests for the scrape orchestration helpers and config validation.

Exercises the boundaries of the scrape pipeline that previously had no
unit-test coverage: _safe_get_json error paths, _collect_from_sources fan-out,
_apply_filters chain, _dedupe_by_url, and validate_config.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline


# ────────────────────────── _safe_get_json ──────────────────────────

class _FakeResponse:
    def __init__(self, *, json_data=None, status_code: int = 200, raise_json: bool = False):
        self._json_data = json_data
        self.status_code = status_code
        self._raise_json = raise_json
        self.response = self  # for RequestException.response access

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests as _r
            err = _r.HTTPError(f"{self.status_code}")
            err.response = self
            raise err

    def json(self):
        if self._raise_json:
            raise ValueError("malformed body")
        return self._json_data


def test_safe_get_json_happy_path(monkeypatch):
    monkeypatch.setattr(
        pipeline.requests, "get",
        lambda *a, **kw: _FakeResponse(json_data={"jobs": [{"id": 1}]}),
    )
    assert pipeline._safe_get_json("http://x", label="x") == {"jobs": [{"id": 1}]}


def test_safe_get_json_returns_none_on_404_silently(monkeypatch, capsys):
    monkeypatch.setattr(
        pipeline.requests, "get",
        lambda *a, **kw: _FakeResponse(status_code=404),
    )
    result = pipeline._safe_get_json("http://x", label="acme/slug")
    assert result is None
    # No noisy stderr for an expected 404 — the slug is just stale.
    err = capsys.readouterr().err
    assert "acme/slug" not in err


def test_safe_get_json_logs_other_http_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        pipeline.requests, "get",
        lambda *a, **kw: _FakeResponse(status_code=500),
    )
    result = pipeline._safe_get_json("http://x", label="acme/slug")
    assert result is None
    assert "acme/slug" in capsys.readouterr().err


def test_safe_get_json_handles_malformed_json(monkeypatch, capsys):
    """Before the helper existed, malformed JSON crashed the whole scrape run."""
    monkeypatch.setattr(
        pipeline.requests, "get",
        lambda *a, **kw: _FakeResponse(raise_json=True),
    )
    result = pipeline._safe_get_json("http://x", label="upstream")
    assert result is None
    assert "invalid JSON" in capsys.readouterr().err


def test_safe_get_json_handles_network_failure(monkeypatch, capsys):
    import requests as _r

    def _raise(*a, **kw):
        raise _r.ConnectionError("dns failed")

    monkeypatch.setattr(pipeline.requests, "get", _raise)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *a, **kw: None)  # no actual sleep in tests
    result = pipeline._safe_get_json("http://x", label="upstream")
    assert result is None
    assert "upstream" in capsys.readouterr().err


def test_safe_get_json_retries_once_on_transient_connection_error(monkeypatch):
    """First call raises ConnectionError, second succeeds. Helper should return data."""
    import requests as _r

    calls = []

    def _flaky_get(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise _r.ConnectionError("transient dns fail")
        return _FakeResponse(json_data={"jobs": [{"id": "x"}]})

    monkeypatch.setattr(pipeline.requests, "get", _flaky_get)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *a, **kw: None)
    result = pipeline._safe_get_json("http://x", label="upstream")
    assert result == {"jobs": [{"id": "x"}]}, "retry should have produced the second-call data"
    assert len(calls) == 2, "should have attempted twice"


def test_safe_get_json_does_not_retry_404(monkeypatch):
    """404s indicate a stale slug, not a transient failure — no retry."""
    calls = []
    monkeypatch.setattr(
        pipeline.requests, "get",
        lambda *a, **kw: (calls.append(1), _FakeResponse(status_code=404))[1],
    )
    monkeypatch.setattr(pipeline.time, "sleep", lambda *a, **kw: None)
    result = pipeline._safe_get_json("http://x", label="acme")
    assert result is None
    assert len(calls) == 1, f"404 should not be retried, but got {len(calls)} attempts"


def test_safe_get_json_does_not_retry_500(monkeypatch):
    """5xx means upstream is reachable but unhappy — also not a retry-worthy transient."""
    calls = []
    monkeypatch.setattr(
        pipeline.requests, "get",
        lambda *a, **kw: (calls.append(1), _FakeResponse(status_code=500))[1],
    )
    monkeypatch.setattr(pipeline.time, "sleep", lambda *a, **kw: None)
    result = pipeline._safe_get_json("http://x", label="upstream")
    assert result is None
    assert len(calls) == 1, f"5xx should not be retried, but got {len(calls)} attempts"


# ────────────────────────── _dedupe_by_url ──────────────────────────

def test_dedupe_preserves_first_occurrence_order():
    jobs = [
        {"url": "https://a", "title": "1"},
        {"url": "https://b", "title": "2"},
        {"url": "https://a", "title": "1-dup"},
        {"url": "https://c", "title": "3"},
    ]
    out = pipeline._dedupe_by_url(jobs)
    assert [j["title"] for j in out] == ["1", "2", "3"]


def test_dedupe_keeps_jobs_with_no_url():
    jobs = [
        {"url": "", "title": "no-url-1"},
        {"url": "", "title": "no-url-2"},
        {"url": "https://a", "title": "with-url"},
    ]
    out = pipeline._dedupe_by_url(jobs)
    # Empty URLs aren't deduped because they're not real identifiers.
    assert len(out) == 3


# ────────────────────────── _collect_from_sources ──────────────────────────

def test_collect_from_sources_fans_out_to_each_enabled_source(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(pipeline, "fetch_greenhouse_jobs", lambda slug: [{"source": "greenhouse", "url": f"gh/{slug}"}])
    monkeypatch.setattr(pipeline, "fetch_lever_jobs", lambda slug: [{"source": "lever", "url": f"lv/{slug}"}])
    monkeypatch.setattr(pipeline, "fetch_ashby_jobs", lambda slug: [{"source": "ashby", "url": f"as/{slug}"}])
    monkeypatch.setattr(pipeline, "fetch_muse_jobs", lambda **kw: [{"source": "themuse", "url": "tm/1"}])

    config = {
        "greenhouse": {"companies": ["acme"]},
        "lever": {"companies": ["beta"]},
        "ashby": {"companies": ["gamma"]},
        "muse": {"categories": ["Software Engineering"]},
    }
    jobs = pipeline._collect_from_sources(config)
    sources = {j["source"] for j in jobs}
    assert sources == {"greenhouse", "lever", "ashby", "themuse"}


def test_collect_from_sources_handles_no_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "BOOTSTRAP_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(pipeline, "fetch_greenhouse_jobs", lambda slug: [])
    monkeypatch.setattr(pipeline, "fetch_lever_jobs", lambda slug: [])
    monkeypatch.setattr(pipeline, "fetch_ashby_jobs", lambda slug: [])
    assert pipeline._collect_from_sources({}) == []


# ────────────────────────── _apply_filters ──────────────────────────

def _job(url: str, title: str = "Data Scientist", location: str = "Remote", html: str = "<p>Python role</p>", posted_at=None) -> dict:
    return {
        "source": "x",
        "title": title,
        "company": "co",
        "location": location,
        "url": url,
        "content_html": html,
        "posted_at": posted_at,
    }


def test_apply_filters_keyword(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "APPLIED_PATH", tmp_path / "applied.json")
    monkeypatch.setattr(pipeline, "SEEN_PATH", tmp_path / "seen.json")
    jobs = [
        _job("a", title="Data Scientist"),
        _job("b", title="Janitor", html="<p>cleaning</p>"),
    ]
    out = pipeline._apply_filters(jobs, {"keywords": ["data"]}, include_seen=False)
    assert [j["url"] for j in out] == ["a"]


def test_apply_filters_skips_applied(monkeypatch, tmp_path):
    applied = tmp_path / "applied.json"
    applied.write_text(json.dumps({"https://a": {}}))
    monkeypatch.setattr(pipeline, "APPLIED_PATH", applied)
    monkeypatch.setattr(pipeline, "SEEN_PATH", tmp_path / "seen.json")
    jobs = [_job("https://a"), _job("https://b")]
    out = pipeline._apply_filters(jobs, {}, include_seen=False)
    assert [j["url"] for j in out] == ["https://b"]


def test_apply_filters_skips_seen_unless_include_seen(monkeypatch, tmp_path):
    seen = tmp_path / "seen.json"
    seen.write_text(json.dumps({"https://a": {"first_seen": "x", "last_seen": "x"}}))
    monkeypatch.setattr(pipeline, "SEEN_PATH", seen)
    monkeypatch.setattr(pipeline, "APPLIED_PATH", tmp_path / "applied.json")
    jobs = [_job("https://a"), _job("https://b")]

    out_default = pipeline._apply_filters(jobs, {}, include_seen=False)
    assert [j["url"] for j in out_default] == ["https://b"]

    out_override = pipeline._apply_filters(jobs, {}, include_seen=True)
    assert {j["url"] for j in out_override} == {"https://a", "https://b"}


# ────────────────────────── validate_config ──────────────────────────

def test_validate_config_accepts_minimal_config():
    pipeline.validate_config({})


def test_validate_config_accepts_full_realistic_config():
    pipeline.validate_config({
        "greenhouse": {"companies": ["acme"]},
        "lever": {"companies": ["beta"]},
        "ashby": {"companies": ["gamma"]},
        "muse": {
            "categories": ["Software Engineering"],
            "levels": ["Senior Level"],
            "locations": ["Remote"],
            "max_pages": 3,
        },
        "keywords": ["python"],
        "max_jobs": 5,
        "posted_within_hours": "7d",
        "location_filter": {"cities": ["Boston"], "exclude": ["india"]},
        "salary_filter": {"min": 100000, "max": 250000},
        "prerank": {"enabled": True, "threshold": 6, "max_candidates": 50},
    })


def test_validate_config_rejects_top_level_typo():
    with pytest.raises(SystemExit) as excinfo:
        pipeline.validate_config({"greenhose": {"companies": ["acme"]}})
    assert "greenhose" in str(excinfo.value)


def test_validate_config_rejects_nested_typo():
    with pytest.raises(SystemExit) as excinfo:
        pipeline.validate_config({"prerank": {"enable": True}})  # typo: enable vs enabled
    # The message should mention the offending key path or a useful hint.
    msg = str(excinfo.value)
    assert "prerank" in msg


# ────────────────────────── Webhook notifications ──────────────────────────

def _result(url: str, score: int, *, title: str = "T", company: str = "C", verdict: str = "qualified") -> dict:
    return {"score": score, "title": title, "company": company, "url": url, "verdict": verdict}


def test_webhook_payload_includes_new_and_total_counts():
    payload = pipeline._build_webhook_payload(
        [_result("a", 80), _result("b", 75)],
        total_results=10,
    )
    assert payload["total_new_top_jobs"] == 2
    assert payload["total_results"] == 10
    assert [j["url"] for j in payload["new_top_jobs"]] == ["a", "b"]
    assert "run_completed_at" in payload  # ISO timestamp for downstream tracking


def test_webhook_skips_post_when_no_new_high_score_jobs(monkeypatch):
    posted = []
    monkeypatch.setattr(pipeline.requests, "post", lambda *a, **kw: posted.append((a, kw)))
    # Every result is below min_score=70.
    results = [_result("a", 60), _result("b", 50)]
    pipeline.post_webhook_notification("http://hook", results, pre_run_seen_urls=set(), min_score=70)
    assert posted == []


def test_webhook_skips_already_seen_jobs(monkeypatch):
    posted = []
    monkeypatch.setattr(pipeline.requests, "post", lambda *a, **kw: posted.append((a, kw)))
    # The high-scoring job was already in seen.json before this run.
    results = [_result("seen-url", 95), _result("new-url", 50)]
    pipeline.post_webhook_notification("http://hook", results, pre_run_seen_urls={"seen-url"}, min_score=70)
    assert posted == []


def test_webhook_posts_when_new_high_score_job(monkeypatch):
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json

    monkeypatch.setattr(pipeline.requests, "post", _fake_post)
    results = [_result("new-url", 85, title="Senior Eng", company="Acme")]
    pipeline.post_webhook_notification("http://hook", results, pre_run_seen_urls=set(), min_score=70)
    assert captured["url"] == "http://hook"
    assert captured["json"]["total_new_top_jobs"] == 1
    assert captured["json"]["new_top_jobs"][0]["title"] == "Senior Eng"
    # Slack-compatible "text" summary is included for nice rendering.
    assert "Senior Eng" in captured["json"]["text"]


def test_webhook_network_failure_does_not_crash(monkeypatch):
    import requests as _r

    def _raise(*a, **kw):
        raise _r.ConnectionError("dns")

    monkeypatch.setattr(pipeline.requests, "post", _raise)
    # No exception escapes.
    pipeline.post_webhook_notification(
        "http://hook",
        [_result("new-url", 90)],
        pre_run_seen_urls=set(),
        min_score=70,
    )


# ────────────────────── prerank rate-limit error tagging ──────────────────────

def _httpx_request():
    import httpx
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _make_rate_limit_error():
    import httpx
    resp = httpx.Response(429, request=_httpx_request())
    return pipeline.anthropic.RateLimitError("rate limited", response=resp, body=None)


def _make_connection_error():
    return pipeline.anthropic.APIConnectionError(request=_httpx_request())


class _RaisingClient:
    """Stand-in anthropic client whose messages.create always raises `exc`."""

    def __init__(self, exc):
        self._exc = exc
        self.messages = self

    def create(self, **kwargs):
        raise self._exc


def _prerank_job():
    # html_to_text must yield a non-empty snippet or prerank short-circuits to 0.
    return {"content_html": "<p>" + "Python data role. " * 20 + "</p>", "title": "T", "company": "C"}


def test_prerank_rate_limit_returns_fallback_and_tags_distinctly():
    pipeline._run_errors.reset()
    client = _RaisingClient(_make_rate_limit_error())
    score = pipeline.prerank_score_one(client, "resume summary", _prerank_job())
    assert score == pipeline.PRERANK_FALLBACK_SCORE
    assert pipeline._run_errors.counts.get("prerank:rate_limit") == 1
    assert "prerank:other" not in pipeline._run_errors.counts


def test_prerank_generic_error_tagged_as_other():
    pipeline._run_errors.reset()
    client = _RaisingClient(_make_connection_error())
    score = pipeline.prerank_score_one(client, "resume summary", _prerank_job())
    assert score == pipeline.PRERANK_FALLBACK_SCORE
    assert pipeline._run_errors.counts.get("prerank:other") == 1
    assert "prerank:rate_limit" not in pipeline._run_errors.counts


def test_run_errors_summary_distinguishes_rate_limit():
    pipeline._run_errors.reset()
    pipeline._run_errors.add("prerank:rate_limit")
    pipeline._run_errors.add("prerank:rate_limit")
    pipeline._run_errors.add("prerank:other")
    s = pipeline._run_errors.summary()
    assert "prerank:rate_limit: 2" in s
    assert "prerank:other: 1" in s
