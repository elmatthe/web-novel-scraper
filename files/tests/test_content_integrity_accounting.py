"""CP4 regressions through synthetic pipeline runs; no network or owner state."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
from pathlib import Path

import pytest

from webnovel_scraper import pdf_builder, pipeline, rescue_pool
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.request_manager import ChallengeFetchError, NotFoundFetchError, RateLimitedFetchError
from webnovel_scraper.run_journal import JOURNAL_FILENAME, SELECTION_FILENAME, replay_journal

_spec = importlib.util.spec_from_file_location("cp4_audit_run", Path(__file__).parents[1] / "audit_run.py")
audit_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_run)


def page(heading="Chapter 1: Test", *, opening=None, body="Original synthetic story prose."):
    return (f"<span class='chapter'>{heading}</span><div id='article'>"
            + (f"<p>{opening}</p>" if opening is not None else "")
            + (f"<p>{body}</p>" if body else "") + "</div>")


class Site:
    def __init__(self, labels, outcomes=None, *, cancel_on_fetch=()):
        self.labels = list(labels)
        self.outcomes = outcomes or {}
        self.fetched = []
        self.cancel_on_fetch = set(cancel_on_fetch)
        self.cancel_event = None

    def build_chapter_index(self, spec, **_kw):
        return [ChapterMeta(float(label), f"{spec.url}/chapter-{position + 1}",
                            slug=position + 1, extra={"manifest_label": str(label)})
                for position, label in enumerate(self.labels)]

    def fetch_chapter(self, meta, spec, **_kw):
        self.fetched.append(meta.slug)
        if meta.slug in self.cancel_on_fetch:
            self.cancel_event.set()
        outcome = self.outcomes.get(meta.slug)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, str):
            return FreeWebNovelAdapter(request_manager=object())._extract_chapter(outcome, meta)
        label = meta.extra.get("manifest_label", str(meta.index))
        return ChapterContent(meta.index, f"Test title {meta.slug}",
                              [f"Original prose from source page {meta.slug}."], label=label)


class Manager:
    def __init__(self, headless):
        self.headless, self.last_fetch_info = headless, None

    def close(self):
        pass


def run(tmp_path, site, *, path="primary", formats=("txt",), mode=OutputMode.SEPARATE,
        stop_after=None, stop_phase=None, pool_builder=None):
    cancel, logs = threading.Event(), []
    site.cancel_event = cancel
    clock = [0.0]

    def advance(seconds):
        clock[0] += seconds

    store = ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime")
    job = ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=1,
                    end=max(1, int(float(site.labels[-1]))), delay=0,
                    output_dir=tmp_path / "out", output_mode=mode, chunk_size=2,
                    use_cache=False, allocate_run=True, formats=formats,
                    use_browser=path != "primary")

    def progress(done, _total):
        if stop_after is not None and done >= stop_after:
            cancel.set()

    def phase_cb(phase, *_details):
        if phase == stop_phase:
            cancel.set()

    kwargs = dict(log=logs.append, cancel_event=cancel, progress_cb=progress,
                  phase_cb=phase_cb, manifest_store=store, sleep_fn=advance,
                  monotonic_fn=lambda: clock[0])
    if path == "primary":
        kwargs["adapter"] = site
    else:
        limiter = HostRateLimiter(0, monotonic=lambda: clock[0], sleep=advance)

        class Primary:
            def build_chapter_index(self, spec, **kw):
                return site.build_chapter_index(spec, **kw)

            def fetch_chapter(self, meta, spec, **kw):
                if path == "rescued":
                    raise ChallengeFetchError("Synthetic primary access challenge")
                return site.fetch_chapter(meta, spec, **kw)

        kwargs.update(
            host_limiter=limiter,
            request_manager_factory=lambda *, headless: Manager(headless),
            primary_adapter_factory=lambda _m: Primary(),
            rescue_pool_factory=lambda: rescue_pool.RescuePool(
                primary_headless=False, cancel_event=cancel, host_limiter=limiter,
                manager_factory=lambda *, headless: Manager(headless),
                adapter_factory=lambda _m: object(), monotonic=lambda: clock[0],
                sleep=advance,
                fetch_fn=lambda meta, **_kw: site.fetch_chapter(meta, None)))
        if pool_builder is not None:
            kwargs["rescue_pool_factory"] = lambda: pool_builder(cancel)
    report = pipeline.run_scrape(job, **kwargs)
    return report, replay_journal(report.output_dir / JOURNAL_FILENAME), logs


@pytest.mark.parametrize("path", ["primary", "rescue-primary", "rescued"])
@pytest.mark.parametrize("html,code,observed", [
    (page("Chapter 9: Wrong"), "IDENTITY_MISMATCH", ["9"]),
    (page("No chapter identity"), "IDENTITY_UNKNOWN", []),
    (page("Chapter 1: Requested", opening="Chapter 9: Other"), "IDENTITY_CONFLICT", ["1", "9"]),
    (page("Chapter 1: Requested", body=""), "EMPTY_BODY", ["1"]),
])
def test_primary_and_rescue_keep_identity_failures_distinct_from_empty_body(tmp_path, path, html, code, observed):
    report, replay, logs = run(tmp_path, Site([1], {1: html}), path=path)
    assert report.failed == [1]
    assert len(replay.failures) == 1
    failure = replay.failures[0]
    assert failure["reason_code"] == code
    assert failure["stage"] == "fetch"
    assert failure["label"] == "1" and failure["page_url"].endswith("chapter-1")
    assert failure["observed_labels"] == observed
    assert not report.written
    if code != "EMPTY_BODY":
        assert not report.extraction_failed
        assert "no usable chapter text" not in failure["reason"]
        assert not any("had no extractable body" in line for line in logs)
    assert report.auto_slowdowns == (1 if path == "rescued" else 0)


@pytest.mark.parametrize("status", [404, 410])
@pytest.mark.parametrize("path", ["primary", "rescue-primary", "rescued"])
def test_http_missing_url_never_certifies_a_displayed_chapter_absent(tmp_path, path, status):
    report, replay, _logs = run(tmp_path, Site([1], {1: NotFoundFetchError(f"HTTP {status}", status=status)}), path=path)
    assert replay.failures[0]["reason_code"] == "URL_NOT_FOUND"
    assert not report.absent_skipped


@pytest.mark.parametrize("path", ["primary", "rescue-primary"])
@pytest.mark.parametrize("mode", list(OutputMode))
def test_stop_preserves_attempted_failures_and_never_invents_unattempted_ones(tmp_path, path, mode):
    site = Site([1, 2, 2, 3], {1: page("Chapter 9: Wrong"),
                              2: page("Chapter 2: Valid", body=""),
                              3: page("Chapter 8: Wrong")})
    report, replay, _logs = run(tmp_path, site, path=path, mode=mode, stop_after=3)
    assert report.cancelled and replay.last_end == "cancelled"
    assert [(f["position"], f["label"]) for f in replay.failures] == [(0, "1"), (1, "2"), (2, "2")]
    assert [f["reason_code"] for f in replay.failures] == ["IDENTITY_MISMATCH", "EMPTY_BODY", "IDENTITY_MISMATCH"]
    assert [f["page_url"].rsplit("-", 1)[-1] for f in replay.failures] == ["1", "2", "3"]
    assert site.fetched == [1, 2, 3]
    assert report.accounting["selected"] == 4
    assert report.accounting["attempted"] == 3
    assert report.accounting["not_attempted"] == 1
    assert not report.written
    selection = (report.output_dir / SELECTION_FILENAME).read_bytes()
    again = audit_run.audit(report.output_dir)
    assert (report.output_dir / SELECTION_FILENAME).read_bytes() == selection
    assert [row["status"] for row in again["chapters"][:3]] == ["failed"] * 3


def test_pdf_only_glyph_fallback_is_real_txt_artifact_but_failed_requested_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_builder, "_fallback_fonts", lambda: [])
    original = "Original Unicode survives: \u6f22\u5b57 \U0001f984."
    report, replay, _logs = run(tmp_path, Site([1], {1: page(body=original)}), formats=("pdf",))
    assert [path.suffix for path in report.written] == [".txt"]
    assert original in report.written[0].read_text(encoding="utf-8")
    assert "1 PDF(s)" not in report.summary()
    assert "TXT" in report.summary()
    row = json.loads((report.output_dir / "run-audit.json").read_text(encoding="utf-8"))["chapters"][0]
    record = replay.publishes[("chapter-00001", "txt")]
    assert row["outputs"]["txt"]["file"] == record["path"]
    assert row["outputs"]["txt"]["sha256"] == hashlib.sha256(report.written[0].read_bytes()).hexdigest()
    assert row["fallback_formats"] == ["txt"]
    assert report.accounting["selected"] == report.accounting["attempted"] == 1
    assert report.accounting["requested_success"] == 0
    assert report.accounting["requested_failure"] == report.accounting["fallback_preserved"] == 1
    audit = audit_run.audit(report.output_dir)
    [fallback] = [f for f in audit["files"] if f.get("file") == record["path"]]
    assert fallback["format"] == "txt" and fallback["state"] == "ok"
    assert fallback["recorded_sha256"] == record["sha256"]
    assert len(audit["chapters"]) == 1


@pytest.mark.parametrize("mode", list(OutputMode))
@pytest.mark.parametrize("body", ["Boom!", "Synthetic prose repeats on different chapters without permission to remove either."])
def test_nonconsecutive_identical_bodies_are_visible_and_preserved_across_groups(tmp_path, mode, body):
    site = Site([1, 2, 3], {1: page("Chapter 1: First title", body=body),
                            3: page("Chapter 3: Different title", body=body)})
    report, replay, logs = run(tmp_path, site, mode=mode)
    assert not report.failed
    assert len(report.written) == {OutputMode.SEPARATE: 3, OutputMode.CHUNKED: 2, OutputMode.SINGLE: 1}[mode]
    assert sum(path.read_text(encoding="utf-8").count(body) for path in report.written) == 2
    assert any("DUPLICATE CONTENT:" in line for line in logs)
    assert any("DUPLICATE CONTENT:" in warning for warning in report.warnings)
    assert report.accounting["integrity_suspicions"] >= 1
    audit = audit_run.audit(report.output_dir)
    assert any("duplicate" in problem.lower() for problem in audit["problems"])
    assert all(f["state"] == "ok" for f in audit["files"])
    assert len(replay.publishes) == len(report.written)


def test_same_label_different_url_and_exact_fractional_identities_remain_distinct(tmp_path):
    labels = ["1", "1", "1.1", "1.10", "2"]
    site = Site(labels, {position: page(f"Chapter {label}: Title", body="An intentionally repeated line.")
                         for position, label in enumerate(labels, 1)})
    report, replay, logs = run(tmp_path, site)
    assert len(report.written) == len({path.name for path in report.written}) == 5
    assert not report.failed
    rows = json.loads((report.output_dir / "run-audit.json").read_text(encoding="utf-8"))["chapters"]
    assert [row["label"] for row in rows] == labels
    assert len({row["page_url"] for row in rows}) == 5
    assert len(replay.publishes) == 5
    assert any("DUPLICATE CONTENT:" in line for line in logs)


@pytest.mark.parametrize("path", ["primary", "rescue-primary"])
def test_stop_before_first_page_records_no_manufactured_failures(tmp_path, path):
    site = Site([1, 2, 3])
    report, replay, _logs = run(tmp_path, site, path=path, stop_phase=pipeline.PHASE_SCRAPING)
    assert report.cancelled and replay.last_end == "cancelled"
    assert site.fetched == [] and replay.failures == [] and not replay.publishes
    assert report.accounting["attempted"] == 0 and report.accounting["not_attempted"] == 3


@pytest.mark.parametrize("path", ["primary", "rescue-primary"])
def test_stop_after_final_page_keeps_every_published_artifact(tmp_path, path):
    report, replay, _logs = run(tmp_path, Site([1, 2, 3]), path=path, stop_after=3)
    assert report.cancelled and replay.last_end == "cancelled"
    assert len(report.written) == len(replay.publishes) == 3
    assert replay.failures == []
    assert report.accounting["attempted"] == report.accounting["requested_success"] == 3
    assert report.accounting["not_attempted"] == 0


@pytest.mark.parametrize("path", ["primary", "rescue-primary"])
def test_stop_during_group_preparation_preserves_failure_without_forcing_render(tmp_path, path):
    site = Site([1, 2, 3], {2: page("Chapter 9: Wrong")})
    report, replay, _logs = run(tmp_path, site, path=path, mode=OutputMode.SINGLE,
                               stop_phase=pipeline.PHASE_BUILDING)
    assert report.cancelled and replay.last_end == "cancelled"
    assert len(replay.failures) == 1 and replay.failures[0]["position"] == 1
    assert replay.failures[0]["reason_code"] == "IDENTITY_MISMATCH"
    assert not replay.publishes and not report.written
    assert report.accounting["attempted"] == 3 and report.accounting["not_attempted"] == 0


@pytest.mark.parametrize("path", ["primary", "rescue-primary"])
def test_stop_after_glyph_fallback_keeps_txt_and_render_failure(tmp_path, monkeypatch, path):
    monkeypatch.setattr(pdf_builder, "_fallback_fonts", lambda: [])
    original = "Synthetic Unicode \u6f22\u5b57."
    report, replay, _logs = run(tmp_path, Site([1, 2], {1: page(body=original)}),
                               path=path, formats=("pdf",), stop_after=1)
    assert report.cancelled and len(report.written) == len(replay.publishes) == 1
    assert original in report.written[0].read_text(encoding="utf-8")
    assert len(replay.failures) == 1
    assert replay.failures[0]["position"] == 0
    assert replay.failures[0]["reason_code"] == "PDF_GLYPH_UNSUPPORTED"
    assert replay.failures[0]["stage"] == "render"
    assert report.accounting["fallback_preserved"] == report.accounting["not_attempted"] == 1


@pytest.mark.parametrize("formats", [("pdf",), ("pdf", "txt")])
def test_successful_formats_count_pages_once_and_artifacts_separately(tmp_path, formats):
    report, replay, _logs = run(tmp_path, Site([1, 2]), formats=formats)
    assert len(report.written) == len(replay.publishes) == len(formats) * 2
    assert report.accounting["selected"] == report.accounting["requested_success"] == 2
    assert report.accounting["requested_failure"] == report.accounting["fallback_preserved"] == 0
    audit = audit_run.audit(report.output_dir)
    assert audit["problems"] == []
    assert all(row["state"] == "ok" for row in audit["files"])
    for record in replay.publishes.values():
        assert hashlib.sha256((report.output_dir / record["path"]).read_bytes()).hexdigest() == record["sha256"]


@pytest.mark.parametrize("path", ["primary", "rescue-primary", "rescued"])
@pytest.mark.parametrize("error,code,http_status", [
    (ValueError("Synthetic unexpected extraction failure"), "EXTRACTION_ERROR", None),
    (ChallengeFetchError("Synthetic Cloudflare interstitial", status=403), "ACCESS_BLOCKED", 403),
    (RateLimitedFetchError("Synthetic HTTP 429", status=429, retry_after=0), "RATE_LIMITED", 429),
])
def test_unexpected_extraction_and_access_outcomes_keep_typed_journal_reason(tmp_path, path, error, code, http_status):
    report, replay, logs = run(tmp_path, Site([1], {1: error}), path=path)
    assert replay.failures
    assert {record["reason_code"] for record in replay.failures} == {code}
    assert {record["stage"] for record in replay.failures} == {"fetch"}
    assert {record["http_status"] for record in replay.failures} == {http_status}
    assert report.accounting["requested_failure"] == 1
    assert not report.extraction_failed and not report.identity_failed
    assert not report.absent_skipped and not report.written
    assert not any("had no extractable body" in line for line in logs)


@pytest.mark.parametrize("path", ["primary", "rescue-primary"])
def test_requested_txt_success_with_failed_pdf_is_not_a_second_fallback_page(tmp_path, monkeypatch, path):
    monkeypatch.setattr(pdf_builder, "_fallback_fonts", lambda: [])
    original = "Synthetic Unicode \u6f22\u5b57."
    report, replay, _logs = run(tmp_path, Site([1], {1: page(body=original)}),
                               path=path, formats=("pdf", "txt"))
    assert len(report.written) == len(replay.publishes) == 1
    assert original in report.written[0].read_text(encoding="utf-8")
    assert report.accounting["selected"] == report.accounting["attempted"] == 1
    assert report.accounting["requested_failure"] == 1
    assert report.accounting["requested_success"] == report.accounting["fallback_preserved"] == 0
    assert report.accounting["formats"]["pdf"]["failure"] == 1
    assert report.accounting["formats"]["txt"]["success"] == 1
    assert not replay.publishes[("chapter-00001", "txt")].get("fallback_for")
    row = json.loads((report.output_dir / "run-audit.json").read_text(encoding="utf-8"))["chapters"][0]
    assert row["requested_format_outcomes"] == {"pdf": "failure", "txt": "success"}
    assert row["fallback_formats"] == []


@pytest.mark.parametrize("path", ["primary", "rescue-primary"])
def test_activity_counts_same_label_failures_by_selected_position(tmp_path, path):
    from webnovel_scraper.ui.run_activity import outcome_events

    site = Site([1, 1, 2], {1: page("Chapter 9: Wrong"), 2: page("Chapter 9: Wrong")})
    report, replay, _logs = run(tmp_path, site, path=path)
    assert len(replay.failures) == report.accounting["requested_failure"] == 2
    assert {failure["position"] for failure in replay.failures} == {0, 1}
    outcome, events = outcome_events(report, processed=(3, 3))
    assert outcome == "completed_with_problems"
    text = "\n".join(event.message for event in events)
    assert "Finished with problems: 2 chapters" in text
    assert "requested-format failure: 2" in text


def test_new_cancelled_journal_replays_and_continues_without_changing_selection(tmp_path):
    report, replay, _logs = run(tmp_path, Site([1, 2, 3], {1: page("Chapter 9: Wrong")}), stop_after=1)
    assert replay.last_end == "cancelled" and len(replay.failures) == 1
    selection_path = report.output_dir / SELECTION_FILENAME
    selection = selection_path.read_bytes()
    fixed_site = Site([1, 2, 3])
    job = ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=1, end=3,
                    delay=0, output_dir=report.output_dir, output_mode=OutputMode.SEPARATE,
                    use_cache=False, formats=("txt",), resume=True)
    resumed = pipeline.run_scrape(job, adapter=fixed_site, log=lambda _m: None,
                                  manifest_store=ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime"),
                                  sleep_fn=lambda _s: None)
    again = replay_journal(report.output_dir / JOURNAL_FILENAME)
    assert resumed.resumed and again.last_end == "complete" and again.sessions == 2
    assert len(again.failures) == 1  # append-only history, resolved by later content/publication
    assert resumed.accounting["requested_failure"] == 0
    assert resumed.accounting["requested_success"] == len(again.publishes) == 3
    assert selection_path.read_bytes() == selection


@pytest.mark.parametrize("error,code", [
    (RateLimitedFetchError("Original synthetic HTTP 429", status=429, retry_after=0), "RATE_LIMITED"),
    (ChallengeFetchError("Original synthetic access challenge", status=403), "ACCESS_BLOCKED"),
])
def test_stop_after_primary_access_outcome_preserves_original_failure_before_recovery(tmp_path, error, code):
    site = Site([1, 2], {1: error}, cancel_on_fetch=(1,))
    report, replay, _logs = run(tmp_path, site, path="rescue-primary")
    assert report.cancelled and replay.last_end == "cancelled"
    assert site.fetched == [1]
    assert len(replay.failures) == 1
    assert replay.failures[0]["position"] == 0
    assert replay.failures[0]["reason_code"] == code
    assert replay.failures[0]["reason"] == str(error)
    assert replay.failures[0]["http_status"] == error.status
    assert report.accounting["attempted"] == report.accounting["requested_failure"] == 1
    assert report.accounting["not_attempted"] == 1
    assert not replay.publishes


def test_stop_with_rescue_queued_keeps_primary_failure_without_inventing_next_page_failure(tmp_path):
    class QueuedPool:
        """A deterministic accepted queue: Stop arrives before its first rescue attempt."""
        _thread = None
        jobs_submitted = jobs_completed = queue_peak = 0
        worker_failed = False

        def __init__(self, cancel):
            self.cancel_event = cancel
            self.results = []

        def start(self):
            return self

        def submit(self, meta):
            self.jobs_submitted += 1
            self.queue_peak = 1
            self.results.append(rescue_pool.RescueResult(meta, None, rescue_pool.CANCELLED))
            self.cancel_event.set()
            return True

        def poll_results(self):
            results, self.results = self.results, []
            return results

        def cancel(self):
            self.cancel_event.set()

        def close(self):
            self.cancel()

    error = ChallengeFetchError("Original queued primary access challenge", status=403)
    site = Site([1, 2], {1: error})
    report, replay, _logs = run(tmp_path, site, path="rescue-primary", pool_builder=QueuedPool)
    assert report.cancelled and site.fetched == [1]
    assert report.rescue_jobs_submitted == 1 and report.rescue_jobs_completed == 0
    assert len(replay.failures) == 1 and replay.failures[0]["position"] == 0
    assert replay.failures[0]["reason_code"] == "ACCESS_BLOCKED"
    assert replay.failures[0]["reason"] == str(error)
    assert report.accounting["attempted"] == report.accounting["not_attempted"] == 1


@pytest.mark.parametrize("old_artifact", ["intact", "missing", "damaged"])
def test_cancelled_refetch_counts_only_intact_previous_artifacts_and_keeps_fetch_evidence(tmp_path, old_artifact):
    initial, _replay, _logs = run(tmp_path, Site([1, 2, 3], {3: page("Chapter 3: Empty", body="")}),
                                 mode=OutputMode.SINGLE)
    assert len(initial.written) == 1
    artifact = initial.written[0]
    if old_artifact == "missing":
        artifact.unlink()  # synthetic file belonging only to this test
    elif old_artifact == "damaged":
        artifact.write_text("Synthetic damaged output", encoding="utf-8")
    before = artifact.read_bytes() if artifact.exists() else None
    selection = (initial.output_dir / SELECTION_FILENAME).read_bytes()
    cancel = threading.Event()
    site = Site([1, 2, 3], {1: page("Chapter 9: Wrong")}, cancel_on_fetch=(1,))
    site.cancel_event = cancel
    job = ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=1, end=3,
                    delay=0, output_dir=initial.output_dir, output_mode=OutputMode.SINGLE,
                    use_cache=False, formats=("txt",), resume=True)
    report = pipeline.run_scrape(job, adapter=site, cancel_event=cancel, log=lambda _m: None,
                                 manifest_store=ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime"),
                                 sleep_fn=lambda _s: None)
    replay = replay_journal(report.output_dir / JOURNAL_FILENAME)
    audit = json.loads((report.output_dir / "run-audit.json").read_text(encoding="utf-8"))
    assert report.cancelled and replay.last_end == "cancelled"
    assert any(f["position"] == 0 and f["reason_code"] == "IDENTITY_MISMATCH" for f in replay.failures)
    assert report.accounting["requested_success"] == (2 if old_artifact == "intact" else 0)
    if old_artifact == "intact":
        assert audit["chapters"][0]["status"] == "saved"
        assert audit["chapters"][0]["requested_format_outcomes"] == {"txt": "success"}
    else:
        assert audit["chapters"][0]["status"] == "failed"
        assert audit["chapters"][0]["outputs"] == {}
    assert audit_run.audit(report.output_dir)["accounting"]["requested_success"] == report.accounting["requested_success"]
    assert (artifact.read_bytes() if artifact.exists() else None) == before
    assert (report.output_dir / SELECTION_FILENAME).read_bytes() == selection
