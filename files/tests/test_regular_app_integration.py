"""Offline contracts for the normal application pipeline (no acceptance runner)."""
import json
import threading

import pytest
from pypdf import PdfReader

from webnovel_scraper import pipeline
from webnovel_scraper.models import ChapterContent, OutputMode
from webnovel_scraper.run_journal import JournalError, load_selection, replay_journal
from .test_run_resume import Site, job, run, resume, store


def test_normal_chunks_publish_before_the_next_fetch(tmp_path, store):
    class Observe(Site):
        def fetch_chapter(self, meta, spec, **kw):
            if meta.index == 3:
                assert len(list((tmp_path / 'out').glob('*/TXT/*.txt'))) == 1
                assert len(list((tmp_path / 'out').glob('*/PDF/*.pdf'))) == 1
            return super().fetch_chapter(meta, spec, **kw)
    report = run(store, job(tmp_path / 'out', end=4, allocate_run=True,
                           mode=OutputMode.CHUNKED, formats=('txt', 'pdf')), Observe(4))
    assert report.accounting['requested_success'] == 4


def test_stop_then_resume_reuses_verified_bodies_without_html_cache(tmp_path, store):
    cancel = threading.Event()
    first = run(store, job(tmp_path / 'out', end=5, allocate_run=True,
                           mode=OutputMode.CHUNKED, chunk=3, formats=('txt', 'pdf')),
                Site(5, cancel=cancel, stop_at=2), cancel=cancel)
    assert first.cancelled
    site = Site(5)
    last = resume(store, first.output_dir, site, end=5, mode=OutputMode.CHUNKED,
                  chunk=3, formats=('txt', 'pdf'))
    assert site.fetched == [3, 4, 5]
    assert last.accounting['requested_success'] == 5
    assert len(list(last.output_dir.glob('PDF/*.pdf'))) == 2


def test_missing_pdf_rebuilds_from_bodies_without_fetching(tmp_path, store):
    first = run(store, job(tmp_path / 'out', end=3, allocate_run=True,
                           mode=OutputMode.SINGLE, formats=('txt', 'pdf')), Site(3))
    txt = next(first.output_dir.glob('TXT/*.txt'))
    before = txt.read_bytes(), txt.stat().st_mtime_ns
    next(first.output_dir.glob('PDF/*.pdf')).unlink()
    class Offline(Site):
        def fetch_chapter(self, *a, **kw):
            pytest.fail('verified saved bodies must not require a request')
    last = resume(store, first.output_dir, Offline(3), end=3, mode=OutputMode.SINGLE,
                  formats=('txt', 'pdf'))
    assert (txt.read_bytes(), txt.stat().st_mtime_ns) == before
    assert last.accounting['requested_success'] == 3
    assert last.from_bodies == 3 and last.skipped_existing == []
    text = ''.join(p.extract_text() for p in PdfReader(next(first.output_dir.glob('PDF/*.pdf'))).pages)
    assert all(f'Body paragraph of chapter {i}' in text for i in (1, 2, 3))


@pytest.mark.parametrize('damage', ['missing', 'changed', 'title'])
def test_saved_body_corruption_refuses_before_journal_mutation(tmp_path, store, damage):
    first = run(store, job(tmp_path / 'out', end=1, allocate_run=True, formats=('txt',)), Site(1))
    folder = first.output_dir
    replay = replay_journal(folder / 'run-journal.jsonl')
    artifact = replay.contents[0]['body_artifact']
    path = folder / artifact['path']
    if damage == 'missing':
        path.unlink()
    else:
        row = json.loads(path.read_text(encoding='utf-8'))
        row['title' if damage == 'title' else 'paragraphs'] = 'Tampered' if damage == 'title' else ['Tampered body.']
        path.write_text(json.dumps(row), encoding='utf-8')
    journal = folder / 'run-journal.jsonl'
    journal.write_bytes(journal.read_bytes() + b'{"torn":')
    before = journal.read_bytes()
    with pytest.raises(JournalError, match='body|Body'):
        resume(store, folder, Site(1), end=1, formats=('txt',))
    assert journal.read_bytes() == before


def test_pdf_text_loss_cannot_receive_a_publication_certificate(tmp_path, store, monkeypatch):
    render = pipeline.render_output
    def broken(fmt, contents, path, title, **kw):
        if fmt == 'pdf':
            from dataclasses import replace
            contents = [replace(c, paragraphs=['Silently substituted body.']) for c in contents]
        return render(fmt, contents, path, title, **kw)
    monkeypatch.setattr(pipeline, 'render_output', broken)
    report = run(store, job(tmp_path / 'out', end=1, allocate_run=True,
                           formats=('txt', 'pdf')), Site(1))
    replay = replay_journal(report.output_dir / 'run-journal.jsonl')
    assert ('chapter-00001', 'pdf') not in replay.publishes
    assert report.accounting['requested_failure'] == 1
    assert replay.last_end == 'complete_with_problems'


def test_composite_heading_survives_body_republication(tmp_path, store):
    class Composite(Site):
        def fetch_chapter(self, meta, spec, **kw):
            return ChapterContent(meta.index, 'Exact title', ['The complete original prose.'],
                                  label='3000-1')
    first = run(store, job(tmp_path / 'out', end=1, allocate_run=True, formats=('txt',)), Composite(1))
    path = first.written[0]
    expected = path.read_bytes()
    path.unlink()
    last = resume(store, first.output_dir, Site(1), end=1, formats=('txt',))
    assert path.read_bytes() == expected
    assert last.accounting['requested_success'] == 1


def test_normal_supreme_adapter_preserves_certified_numeric_title():
    from webnovel_scraper import catalog
    from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
    from webnovel_scraper.models import ChapterMeta
    spec = catalog.get_spec('supreme-magus', 'freewebnovel')
    class Page:
        def fetch(self, *args, **kwargs):
            return '<h1>Chapter 2475: 2475 Original wording</h1><div id="article"><p>Original prose.</p></div>'
    meta = ChapterMeta(2475, spec.url + '/chapter-2486', title='2475 Original wording', slug=2486)
    content = FreeWebNovelAdapter(Page()).fetch_chapter(meta, spec)
    assert content.title == '2475 Original wording'


@pytest.mark.parametrize('observed', ['Different title', ''])
def test_normal_supreme_adapter_refuses_title_drift_from_verified_inventory(observed):
    from webnovel_scraper import catalog
    from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
    from webnovel_scraper.models import ChapterMeta, ChapterMismatchError
    spec = catalog.get_spec('supreme-magus', 'freewebnovel')
    class Page:
        def fetch(self, *args, **kwargs):
            return f'<h1>Chapter 42: {observed}</h1><div id="article"><p>Original prose.</p></div>'
    meta = ChapterMeta(42, spec.url + '/chapter-44', title='Certified title', slug=44)
    with pytest.raises(ChapterMismatchError, match='title'):
        FreeWebNovelAdapter(Page()).fetch_chapter(meta, spec)


def test_body_directory_links_refuse_resume_and_are_not_walked_by_audit(tmp_path, store):
    from webnovel_scraper.run_storage import RunStorageError
    from .test_run_storage import make_directory_link
    from .test_audit_run import audit_run
    first = run(store, job(tmp_path / 'out', end=1, allocate_run=True, formats=('txt',)), Site(1))
    folder = first.output_dir
    bodies = folder / 'chapter-bodies'
    original = tmp_path / 'original-bodies'
    assert bodies.resolve().is_relative_to(tmp_path.resolve())
    assert original.resolve().is_relative_to(tmp_path.resolve())
    bodies.rename(original)
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'owner.txt'
    sentinel.write_bytes(b'preserve linked content')
    make_directory_link(bodies, outside)
    journal = folder / 'run-journal.jsonl'
    before = journal.read_bytes()
    with pytest.raises(RunStorageError, match='[Ll]ink|[Rr]eparse'):
        resume(store, folder, Site(1), end=1, formats=('txt',))
    report = audit_run.audit(folder)
    assert not report['semantic_resume']['eligible']
    assert not any(row['path'].startswith('chapter-bodies/') for row in report['unrecorded_files'])
    assert sentinel.read_bytes() == b'preserve linked content'
    assert journal.read_bytes() == before
