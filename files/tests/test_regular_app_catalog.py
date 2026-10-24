"""Real adapters and GUI callbacks against synthetic HTML; no live source access."""
from dataclasses import replace
from html import escape
import json
import re
import threading

import pytest

import app
from webnovel_scraper import catalog, pipeline
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import OutputMode, ScrapeJob
from webnovel_scraper.registry import REGISTRY
from webnovel_scraper.run_journal import load_selection, replay_journal
from webnovel_scraper.run_output import plan_groups
from webnovel_scraper.ui.selection import website_key
from .test_freewebnovel_net import list_page, NOVEL_PATH

ENABLED = [s for s in catalog.all_specs() if s.enabled]
FRACTIONS = ('627.2', '627.3', '627.4', '627.5', '627.6', '952.2', '952.3', '1923.1', '1923.2')


class Pages:
    """Only the network boundary is replaced; registry adapters parse every page."""
    def __init__(self, spec, labels):
        self.spec, self.labels = spec, list(labels)
        self.calls = []
        self.cancel_event = threading.Event()
        self.last_fetch_info = None
        self.headless = True

    def start(self):
        return self

    def close(self):
        pass

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        s, labels = self.spec, self.labels
        if url.startswith(s.url) and (url == s.url or '?page=' in url):
            if s.adapter_key == 'webnovel_dynamic':
                items = [dict(chapterIndex=i, chapterId=str(100000 + i), chapterName=f'Chapter {label}: Title {label}')
                         for i, label in reversed(list(enumerate(labels, 1)))]
                data = dict(bookInfo=dict(bookId=s.book_id, chapterCnt=len(labels)),
                            volumeItems=[dict(chapterItems=items)])
                return '<script id="__NEXT_DATA__">' + json.dumps(dict(props=dict(pageProps=dict(data=data)))) + '</script>'
            if s.adapter_key == 'freewebnovel_net':
                page = int(url.split('?page=')[1]) if '?page=' in url else 1
                return list_page(len(labels), page).replace(NOVEL_PATH, s.url.split('.net')[1].removesuffix('.html'))
            return ''.join(f'<a href="{s.url}/chapter-{i}">Chapter {label}: Title {label}</a>'
                           for i, label in reversed(list(enumerate(labels, 1))))
        match = re.search(r'/chapter-(\d+)', url)
        position = int(match[1]) if match else int(url.rsplit('/', 1)[1]) - 100000
        label = labels[position - 1]
        title = f'Chapter {label}: Title {label}'
        body = escape(f'Unique original story paragraph {position}; Ω “quotes” & <symbols>.')
        if s.adapter_key == 'webnovel_dynamic':
            ci = dict(chapterIndex=position, chapterId=str(100000 + position), bookId=s.book_id,
                      chapterName=title, contents=[dict(content=body)])
            data = {'props': {'pageProps': {'data': {'chapterInfo': ci}}}}
            return '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
        if s.adapter_key == 'freewebnovel_net':
            return f'<h1 class="chapter">{title}</h1><div id="chapter-content"><p>{body}</p></div>'
        return f'<h1>{title}</h1><div id="article"><p>{body}</p></div>'


def execute(job, pages, store, **kwargs):
    # Exercise the normal browser conductor for .com and the owned HTTP manager
    # path for .net/WebNovel, with real registry adapters in both paths.
    return pipeline.run_scrape(
        job, manifest_store=store, sleep_fn=lambda _: None,
        request_manager=pages, request_manager_factory=lambda **_: pages,
        primary_adapter_factory=lambda manager: REGISTRY[job.adapter_key](manager), **kwargs)


@pytest.mark.parametrize('spec', ENABLED, ids=lambda s: f'{s.novel_slug}-{s.adapter_key}')
@pytest.mark.parametrize('mode,formats', [
    (OutputMode.SEPARATE, ('txt',)), (OutputMode.CHUNKED, ('pdf', 'txt')),
    (OutputMode.SINGLE, ('pdf',)),
])
def test_gui_selection_range_all_publication_and_resume(tmp_path, monkeypatch, spec, mode, formats):
    labels = ['1', '2', '2.1', '2.10', '3', '4', '5', '6', '7'] if spec.novel_slug == 'supreme-magus' else list(map(str, range(1, 8)))
    pages = Pages(spec, labels)
    store = ChapterManifestStore(tmp_path / 'baseline', tmp_path / 'runtime')
    reports = []
    real_run = pipeline.run_scrape
    def offline(job, **kwargs):
        # execute() must call the original pipeline through this patched module.
        monkeypatch.setattr(pipeline, 'run_scrape', real_run)
        try:
            report = execute(job, pages, store, **kwargs)
            reports.append(report)
            return report
        finally:
            monkeypatch.setattr(pipeline, 'run_scrape', offline)
    monkeypatch.setattr(pipeline, 'run_scrape', offline)
    win = app.ScraperApp()
    win.withdraw()
    try:
        win._select(website_key(spec), spec.novel_slug)
        assert win._current_spec() == spec
        win._output_parent = tmp_path / 'outputs'
        win._cache_var.set(False)
        win._mode_var.set(mode.value)
        win._chunk_var.set('3')
        win._pdf_var.set('pdf' in formats)
        win._txt_var.set('txt' in formats)
        monkeypatch.setattr(win, '_launch', lambda job, *_a, **_kw: win._run_worker(job))
        # Real Start validation -> job -> worker -> pipeline -> outcome events.
        for start, end in [('2', '4'), ('1', '')]:
            win._start_var.set(start)
            win._end_var.set(end)
            # The second selection asks to validate coverage beyond the first run.
            win._refresh_var.set(start == '1')
            win._on_start()
            win.flush_activity()
            assert len(reports) == (1 if start == '2' else 2)
            report = reports[-1]
            expected = [label for label in labels if int(start) <= float(label) <= (int(end) if end else 7)]
            replay = replay_journal(report.output_dir / 'run-journal.jsonl')
            selected = load_selection(report.output_dir, replay.header)
            assert [c.label.text for c in selected] == expected
            assert report.accounting['requested_success'] == len(expected)
            assert replay.last_end == 'complete'
            assert all(replay.publishes[g.key, fmt]['certificate']
                       for g in plan_groups(len(expected), mode, 3)
                       for fmt in formats)
        before_calls = len(pages.calls)
        assert win.start_resume(reports[-1].output_dir)
        win.flush_activity()
        assert reports[-1].resumed and len(pages.calls) == before_calls
    finally:
        win.destroy()


@pytest.mark.parametrize('novel,key,end', [
    ('supreme-magus', 'freewebnovel', 4552),
    ('lord-of-the-mysteries', 'freewebnovel_net', 2501),
    ('the-noble-queen', 'webnovel_dynamic', 1001),
])
def test_full_range_real_adapter_pipeline_simulation(tmp_path, novel, key, end):
    spec = catalog.get_spec(novel, key)
    labels = []
    for n in range(1, end + 1):
        labels.append(str(n))
        if novel == 'supreme-magus':
            labels.extend(f for f in FRACTIONS if f.startswith(f'{n}.'))
    pages = Pages(spec, labels)
    store = ChapterManifestStore(tmp_path / 'baseline', tmp_path / 'runtime')
    job = ScrapeJob(novel, key, 1, 10**9, 0, OutputMode.CHUNKED, False, tmp_path / 'outputs',
                    formats=('txt',), chunk_size=100, allocate_run=True, use_browser=spec.use_browser)
    report = execute(job, pages, store, log=lambda _: None)
    replay = replay_journal(report.output_dir / 'run-journal.jsonl')
    selected = load_selection(report.output_dir, replay.header)
    assert [c.label.text for c in selected] == labels
    assert report.accounting['requested_success'] == len(labels)
    assert replay.last_end == 'complete'
    assert len(replay.publishes) == (len(labels) + 99) // 100
    actual = []
    for record in replay.publishes.values():
        actual.extend(re.findall(r'^Chapter ([0-9.]+):',
                      (report.output_dir / record['path']).read_text(encoding='utf-8'), re.M))
    assert actual == labels
    before_calls = len(pages.calls)
    resumed = execute(replace(job, output_dir=report.output_dir, allocate_run=False, resume=True),
                      pages, store, log=lambda _: None)
    assert resumed.accounting['requested_success'] == len(labels) and len(pages.calls) == before_calls
