"""freewebnovel.net adapter and the two Lord of the Mysteries catalog pairings (v0.3.0 04).

Offline: list and chapter pages are synthetic, built to the structure recorded live on 2026-10-18
(page selector ``#indexselect`` with ``data-url`` options and "C.a - C.b" ranges, ``#idData`` entries,
the ``.m-newest1`` latest-chapters widget, ``h1.chapter`` + ``#chapter-content`` chapter pages).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.adapters.freewebnovel_net import FreeWebNovelNetAdapter, parse_list_page
from webnovel_scraper.chapter_preflight import ChapterListRefused, resolve_chapters
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import ChapterIndex, ChapterMeta, ChapterMismatchError, OutputMode, ScrapeJob
from webnovel_scraper.registry import REGISTRY
from webnovel_scraper.source_identity import SourceIdentity
from webnovel_scraper.ui import selection

NET = catalog.get_spec("lord-of-mysteries-2-circle-of-inevitability", "freewebnovel_net")
BASE = "https://freewebnovel.net"
NOVEL_PATH = "/lord-of-mysteries-2-circle-of-inevitability"


def chapter_url(n: int) -> str:
    return f"{BASE}{NOVEL_PATH}/chapter-{n}-{n}-title-{n}.html"


def list_page(total: int, page: int, *, latest_top=None, short_last=False, extra_anchors="") -> str:
    pages = (total + 39) // 40
    options = "".join(
        f'<option data-url="{NOVEL_PATH}.html{"" if p == 1 else f"?page={p}"}" value="{p}">'
        f"C.{(p - 1) * 40 + 1} - C.{min(p * 40, total)}</option>" for p in range(1, pages + 1))
    first, last = (page - 1) * 40 + 1, min(page * 40, total)
    if short_last and page == pages:
        last -= 1
    entries = "".join(
        f'<li><a class="con" href="{NOVEL_PATH}/chapter-{n}-{n}-title-{n}.html" '
        f'title="Chapter {n} - {n} Title &amp;quot;{n}&amp;quot;"><span>Chapter {n}</span></a></li>'
        for n in range(first, last + 1))
    top = total if latest_top is None else latest_top
    latest = "".join(f'<li><a href="{NOVEL_PATH}/chapter-{n}-{n}-title-{n}.html">Chapter {n}</a></li>'
                     for n in range(top, max(0, top - 6), -1))
    return (f"<html><body><div class='m-newest1'><ul class='ul-list5'>{latest}</ul></div>"
            f"<ul class='ul-list5' id='idData'>{entries}{extra_anchors}</ul>"
            f"<div class='page'><select id='indexselect'>{options}</select></div>"
            "<a href='/ajax-chapter-list?x=1'>never</a></body></html>")


def chapter_page(n: int, *, shows=None) -> str:
    shown = n if shows is None else shows
    return (f"<html><body><div class='m-read' id='chapter'><div class='top'><h2 class='tit'>"
            f"<a href='{NOVEL_PATH}.html'>LOTM2</a></h2><h1 class='chapter'><span class='chapter-text'>"
            f"Chapter {shown} - {shown} Title {shown}</span></h1></div>"
            "<div class='tts'><p>Uses your browser's voices.</p></div>"
            f"<div id='chapter-content'><p>{shown} Title {shown}</p><p>Painful!</p>"
            f"<p>Body of chapter {shown}.</p></div><div><p>We can fix it as soon as possible.</p></div>"
            "</div></body></html>")


class FakeRM:
    def __init__(self, total, **page_kw):
        self.total, self.page_kw = total, page_kw
        self.fetched: list[tuple[str, object]] = []
        self.last_fetch_info = None

    def fetch(self, url, *, use_browser=False, use_cache=None, fast_path=False):
        self.fetched.append((url, use_cache))
        assert "/ajax-" not in url, "robots.txt disallows /ajax-"
        if url.startswith(f"{BASE}{NOVEL_PATH}.html"):
            page = int(url.split("page=")[1]) if "page=" in url else 1
            return list_page(self.total, page, **self.page_kw)
        n = int(url.rsplit("/chapter-", 1)[1].split("-")[0])
        return chapter_page(n)

    def list_pages_read(self):
        return sorted(int(u.split("page=")[1]) if "page=" in u else 1 for u, _ in self.fetched
                      if u.startswith(f"{BASE}{NOVEL_PATH}.html"))


def adapter(total=92, **kw):
    rm = FakeRM(total, **kw)
    return rm, FreeWebNovelNetAdapter(request_manager=rm, log=lambda _m: None)


# ── catalog and identity ───────────────────────────────────────────────────────
def test_both_novels_are_offered_on_both_hosts_as_separate_websites():
    for slug in ("lord-of-the-mysteries", "lord-of-mysteries-2-circle-of-inevitability"):
        com = catalog.get_spec(slug, "freewebnovel")
        net = catalog.get_spec(slug, "freewebnovel_net")
        assert com.enabled and net.enabled
        assert SourceIdentity.from_site_spec(com).site_key == "freewebnovel.com"
        assert SourceIdentity.from_site_spec(net).site_key == "freewebnovel.net"
        assert com.url == f"https://freewebnovel.com/novel/{slug}" and com.use_browser
        assert net.url == f"https://freewebnovel.net/{slug}.html" and not net.use_browser
    sites = {w.key: w for w in selection.websites(catalog.all_specs())}
    assert sites["freewebnovel.com"].label == "Free Web Novel (.com)"
    assert sites["freewebnovel.net"].label == "Free Web Novel (.net)" and sites["freewebnovel.net"].enabled
    assert [n.slug for n in selection.novels_for("freewebnovel.net", catalog.all_specs())] == [
        "lord-of-the-mysteries", "lord-of-mysteries-2-circle-of-inevitability"]
    assert REGISTRY["freewebnovel_net"] is FreeWebNovelNetAdapter


def test_the_disabled_stub_sites_stay_disabled():
    for key in ("empire_novel", "telegraph", "novel_bin"):
        assert all(not s.enabled for s in catalog.all_specs() if s.adapter_key == key)


# ── chapter-list discovery ─────────────────────────────────────────────────────
def test_a_full_discovery_reads_every_list_page_fresh_and_confirms_the_tail():
    rm, a = adapter(92)
    metas = a.build_chapter_index(NET)
    assert [m.extra["manifest_label"] for m in metas] == [str(n) for n in range(1, 93)]
    assert metas[0].url == chapter_url(1) and metas[0].title == 'Title "1"'   # number + entity cleaned
    assert metas.tail_confirmed is True
    assert rm.list_pages_read() == [1, 2, 3]
    assert all(use_cache is False for _u, use_cache in rm.fetched)              # never a cached list


@pytest.mark.parametrize("destination", [
    "http://127.0.0.1:8765/private", "https://example.org/list",
    "https://freewebnovel.net/ajax-chapter-list", "/other-novel.html?page=2",
    f"{NOVEL_PATH}.html?page=3", f"{NOVEL_PATH}.html?page=2&redirect=private",
    f"https://user@freewebnovel.net{NOVEL_PATH}.html?page=2",
    f"https://freewebnovel.net:8443{NOVEL_PATH}.html?page=2",
    f"{NOVEL_PATH}.html?page=2#fragment",
])
def test_pagination_refuses_untrusted_selector_urls_before_fetch(destination):
    rm, a = adapter(60)
    original_fetch = rm.fetch

    def fetch(url, **kw):
        page = original_fetch(url, **kw)
        return page.replace(f'{NOVEL_PATH}.html?page=2', destination)

    rm.fetch = fetch
    with pytest.raises(ValueError, match="pagination"):
        a.build_chapter_index(NET)
    assert rm.fetched == [(NET.url, False)]


def test_a_bounded_window_reads_only_the_pages_around_it():
    rm, a = adapter(232)                                                        # 6 pages
    metas = a.build_chapter_index(NET, start_hint=85, end_hint=90)
    assert rm.list_pages_read() == [1, 2, 3, 4]                                 # page 3 +- 1, plus page 1
    labels = {m.extra["manifest_label"] for m in metas}
    assert {"85", "90"} <= labels and "200" not in labels
    assert metas.tail_confirmed is False                                        # last page not read


def test_an_open_ended_window_always_reads_the_last_page():
    rm, a = adapter(232)
    metas = a.build_chapter_index(NET, start_hint=170, end_hint=None)
    assert rm.list_pages_read() == [1, 4, 5, 6]
    assert metas[-1].extra["manifest_label"] == "232" and metas.tail_confirmed


def test_a_list_that_disagrees_with_its_latest_widget_never_confirms_its_tail():
    _rm, a = adapter(92, latest_top=95)
    assert a.build_chapter_index(NET).tail_confirmed is False
    assert any("latest-chapters widget" in w for w in a.warnings)


def test_a_short_last_page_never_confirms_its_tail():
    _rm, a = adapter(92, short_last=True)
    assert a.build_chapter_index(NET).tail_confirmed is False


class ListRM(FakeRM):
    """List pages of *positions* entries, 40 a page. With ``drift`` the site's list skips chapters
    101-160, so the entry at position p > 100 is chapter p + 60. ``ranges=False`` labels the page
    selector "Page k" (no "C.a - C.b" range); ``widget=False`` drops the latest-chapters widget."""

    def __init__(self, positions, *, drift=False, ranges=True, widget=True):
        super().__init__(positions)
        self.labels = [p + 60 if drift and p > 100 else p for p in range(1, positions + 1)]
        self.ranges, self.widget = ranges, widget

    def fetch(self, url, *, use_browser=False, use_cache=None, fast_path=False):
        self.fetched.append((url, use_cache))
        page = int(url.split("page=")[1]) if "page=" in url else 1
        count = len(self.labels)
        options = "".join(
            f'<option data-url="{NOVEL_PATH}.html{"" if p == 1 else f"?page={p}"}" value="{p}">'
            + (f"C.{(p - 1) * 40 + 1} - C.{min(p * 40, count)}" if self.ranges else f"Page {p}")
            + "</option>" for p in range(1, (count + 39) // 40 + 1))
        entries = "".join(f'<li><a href="{NOVEL_PATH}/chapter-{n}-t.html" title="Chapter {n} - T{n}">x</a></li>'
                          for n in self.labels[(page - 1) * 40: page * 40])
        top = self.labels[-1]
        latest = (f"<div class='m-newest1'><a href='{NOVEL_PATH}/chapter-{top}-t.html'>Chapter {top}</a></div>"
                  if self.widget else "")
        return (f"<html><body>{latest}<ul id='idData'>{entries}</ul>"
                f"<select id='indexselect'>{options}</select></body></html>")


def _resolve(tmp_path, discover, start, end):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    return resolve_chapters(NET, start=start, end=end, store=store, discover=discover, log=lambda _m: None)


def test_a_list_numbered_above_its_positions_widens_down_to_start_and_invents_no_gap(tmp_path):
    rm = ListRM(400, drift=True)
    a = FreeWebNovelNetAdapter(request_manager=rm, log=lambda _m: None)
    metas = a.build_chapter_index(NET, start_hint=300, end_hint=320)
    assert rm.list_pages_read() == [1, 6, 7, 8, 9]                       # page 6 holds chapter 300
    assert metas.examined == ((1, 40), (261, 420))
    selection_ = _resolve(tmp_path, lambda **kw: FreeWebNovelNetAdapter(
        request_manager=ListRM(400, drift=True), log=lambda _m: None).build_chapter_index(NET, **kw), 300, 320)
    assert selection_.toc_gaps == [] and [int(m.index) for m in selection_.chapters] == list(range(300, 321))


def test_numbers_between_the_list_pages_read_are_never_called_gaps(tmp_path):
    numbers = [*range(1, 41), *range(261, 301)]
    index = ChapterIndex([ChapterMeta(index=n, url=chapter_url(n), extra={"manifest_label": str(n)})
                          for n in numbers], tail_confirmed=False, examined=((1, 40), (261, 300)))
    with pytest.raises(ChapterListRefused, match="200-260"):
        _resolve(tmp_path, lambda **_kw: index, 200, 280)


def test_a_page_selector_without_ranges_is_estimated_and_never_vouches_for_the_end(tmp_path):
    rm = ListRM(400, ranges=False, widget=False)
    a = FreeWebNovelNetAdapter(request_manager=rm, log=lambda _m: None)
    metas = a.build_chapter_index(NET, start_hint=1, end_hint=100)
    assert set(range(1, 101)) <= {int(m.index) for m in metas} and metas.tail_confirmed is False
    assert any("does not say which chapters each page holds" in w for w in a.warnings)

    def discover(**kw):
        return FreeWebNovelNetAdapter(request_manager=ListRM(400, ranges=False, widget=False),
                                      log=lambda _m: None).build_chapter_index(NET, **kw)

    assert _resolve(tmp_path / "a", discover, 1, 100).effective_range == (1, 100)   # not clamped to 40
    with pytest.raises(ChapterListRefused, match="could not confirm"):
        _resolve(tmp_path / "b", discover, 390, 500)                               # no End clamp


def test_entries_off_the_novel_or_off_the_host_are_ignored_and_unnumbered_ones_reported():
    extra = ('<li><a href="/another-novel/chapter-1-x.html" title="Chapter 1 - X">x</a></li>'
             '<li><a href="https://evil.example/lord-of-mysteries-2-circle-of-inevitability/chapter-9-a.html" '
             'title="Chapter 9 - A">y</a></li>'
             f'<li><a href="{NOVEL_PATH}/chapter-side-story.html" title="Side Story">z</a></li>')
    html = list_page(5, 1, extra_anchors=extra)
    page = parse_list_page(html, NET, 1)
    assert [e[0] for e in page.entries] == ["1", "2", "3", "4", "5"]
    assert page.unnumbered == ["Side Story"]
    assert page.ranges == {1: (1, 5)} and page.latest[0] == "5"


# ── chapter pages ─────────────────────────────────────────────────────────────
def test_a_chapter_reads_only_its_body_with_one_canonical_heading():
    rm, a = adapter(5)
    content = a.fetch_chapter(ChapterMeta(index=1, url=chapter_url(1), title="Title 1",
                                          extra={"manifest_label": "1"}), NET)
    assert content.heading == "Chapter 1: Title 1"
    assert content.paragraphs == ["Painful!", "Body of chapter 1."]


def test_a_chapter_page_showing_another_number_is_refused():
    _rm, a = adapter(5)
    with pytest.raises(ChapterMismatchError):
        a._extract_chapter(chapter_page(3, shows=4), ChapterMeta(index=3, url=chapter_url(3)))


# ── end to end: .net and .com stay apart ──────────────────────────────────────
def test_a_net_run_saves_its_own_list_cache_and_folder_apart_from_com(tmp_path, monkeypatch):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    rm, a = adapter(92)
    job = ScrapeJob(novel_slug=NET.novel_slug, adapter_key="freewebnovel_net", start=1, end=500, delay=0.0,
                    output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=tmp_path / "out",
                    allocate_run=True, formats=("pdf", "txt"))
    report = pipeline.run_scrape(job, adapter=a, request_manager=rm, log=lambda _m: None,
                                 sleep_fn=lambda _s: None, manifest_store=store)
    assert report.effective_range == (1, 92)                                    # End clamped
    assert report.output_dir.name == "lord-of-mysteries-2-circle-of-inevitability-1"
    assert len(list((report.output_dir / "TXT").glob("*.txt"))) == 92
    net_source = SourceIdentity.from_site_spec(NET)
    com_source = SourceIdentity.from_site_spec(catalog.get_spec(NET.novel_slug, "freewebnovel"))
    assert store.runtime_path(net_source).parent.name == "freewebnovel.net"
    assert store.runtime_path(net_source) != store.runtime_path(com_source)
    assert not store.runtime_path(com_source).exists()
    text = (report.output_dir / "TXT" / "Chapter 7_ Title 7.txt").read_text(encoding="utf-8")
    assert text.startswith("Chapter 7: Title 7\n\nPainful!")


def test_the_standard_path_paces_list_pages_like_chapters(tmp_path):
    """The standard (non-browser) path now gives its own request manager a per-host limiter, so a
    paginated list is never read back to back (only when the clock pair is consistent)."""
    import time

    assert pipeline._legacy_limiter(
        ScrapeJob(novel_slug="x", adapter_key="y", start=1, end=1, delay=4.0, output_mode=OutputMode.SEPARATE,
                  use_cache=False, output_dir=tmp_path), time.monotonic, time.sleep).interval == 4.0
    assert pipeline._legacy_limiter(
        ScrapeJob(novel_slug="x", adapter_key="y", start=1, end=1, delay=0.0, output_mode=OutputMode.SEPARATE,
                  use_cache=False, output_dir=tmp_path), time.monotonic, lambda _s: None) is None


# ── irregular chapter addresses (owner acceptance, 2026-10-18) ─────────────────
LOTM = catalog.get_spec("lord-of-the-mysteries", "freewebnovel_net")
# The five list entries for Lord of the Mysteries 1301-1305 exactly as freewebnovel.net served them
# on 2026-10-18 (links and titles only): the site spells chapter 1303's address "chaspter-".
REAL_1301_1305 = (
    '<li><a href="/lord-of-the-mysteries/chapter-1301-new-life.html" class="con" '
    'title="Chapter 1301 - New Life\u201c"><span class="chapter-text">Chapter 1301 - New Life</span></a></li>'
    '<li><a href="/lord-of-the-mysteries/chapter-1302-the-scene-in-the-prophecy.html" class="con" '
    'title="Chapter 1302 - The Scene in the Prophecy\xa0"><span>Chapter 1302</span></a></li>'
    '<li><a href="/lord-of-the-mysteries/chaspter-1303-coming-to-life.html" class="con" '
    'title="Chapter 1303 - Coming to Life"><span class="chapter-text">Chapter 1303 - Coming to Life</span></a></li>'
    '<li><a href="/lord-of-the-mysteries/chapter-1304-the-importance-of-ideas.html" class="con" '
    'title="Chapter 1304 -  The Importance of Ideas"><span>Chapter 1304</span></a></li>'
    '<li><a href="/lord-of-the-mysteries/chapter-1305-that-black-shadow.html" class="con" '
    'title="Chapter 1305  - That Black Shadow"><span>Chapter 1305</span></a></li>'
)
REAL_1303 = "https://freewebnovel.net/lord-of-the-mysteries/chaspter-1303-coming-to-life.html"


def _lotm_page(entries: str) -> str:
    return (f"<html><body><ul id='idData'>{entries}</ul><select id='indexselect'>"
            "<option data-url='/lord-of-the-mysteries.html' value='1'>C.1301 - C.1305</option>"
            "</select></body></html>")


def test_the_real_irregular_1303_address_is_listed_as_the_site_spells_it():
    page = parse_list_page(_lotm_page(REAL_1301_1305), LOTM, 1)
    assert [e[0] for e in page.entries] == ["1301", "1302", "1303", "1304", "1305"]
    assert page.entries[2] == ("1303", "Coming to Life", REAL_1303)          # not autocorrected
    assert page.irregular == [("1303", "/lord-of-the-mysteries/chaspter-1303-coming-to-life.html")]
    assert page.unnumbered == []


@pytest.mark.parametrize("href, title", [
    ("/lord-of-the-mysteries/chaspter-1304-coming-to-life.html", "Chapter 1303 - Coming to Life"),  # numbers differ
    ("/lord-of-the-mysteries/chaspter-coming-to-life.html", "Chapter 1303 - Coming to Life"),       # no number
    ("/lord-of-the-mysteries/chaspter-1303-coming-to-life.html", "Coming to Life"),                 # no label
    ("/lord-of-the-mysteries/x/chaspter-1303-coming.html", "Chapter 1303 - Coming"),                # deeper path
    ("/lord-of-the-mysteries/chaspter-1303-coming.php", "Chapter 1303 - Coming"),                   # not a page
    ("/lord-of-the-mysteries/chaspter-1303.5-coming.html", "Chapter 1303.5 - Coming"),              # not whole
    ("/another-novel/chaspter-1303-coming.html", "Chapter 1303 - Coming"),                          # other novel
    ("/lord-of-the-mysteries-2/chaspter-1303-coming.html", "Chapter 1303 - Coming"),                # prefix novel
    ("http://freewebnovel.net/lord-of-the-mysteries/chaspter-1303-c.html", "Chapter 1303 - C"),     # not https
    ("https://evil.example/lord-of-the-mysteries/chaspter-1303-c.html", "Chapter 1303 - C"),        # other host
    ("https://freewebnovel.net.evil.example/lord-of-the-mysteries/chaspter-1303-c.html", "Chapter 1303 - C"),
    ("javascript:alert(1303)//lord-of-the-mysteries/chaspter-1303-c.html", "Chapter 1303 - C"),
    ("/lord-of-the-mysteries/chaspter-1303-c%2F..%2F..%2Fx.html", "Chapter 1303 - C"),              # encoded path
    ("/lord-of-the-mysteries.html?page=33", "Chapter 1303 - C"),                                    # list page
])
def test_irregular_or_unsafe_links_are_never_taken_as_chapters(href, title):
    page = parse_list_page(_lotm_page(f'<li><a href="{href}" title="{title}">x</a></li>'), LOTM, 1)
    assert page.entries == [] and page.irregular == []


class IrregularRM(FakeRM):
    """Lord of the Mysteries list pages (40 a page) whose entry for chapter 1303 has the site's
    "chaspter-" address; chapter pages are served for any address carrying their number."""

    def __init__(self, total):
        super().__init__(total)
        self.path = "/lord-of-the-mysteries"

    def fetch(self, url, *, use_browser=False, use_cache=None, fast_path=False):
        self.fetched.append((url, use_cache))
        if url.startswith(f"{BASE}{self.path}.html"):
            page = int(url.split("page=")[1]) if "page=" in url else 1
            html = list_page(self.total, page).replace(NOVEL_PATH, self.path)
            return html.replace(f"{self.path}/chapter-1303-", f"{self.path}/chaspter-1303-")
        n = int(url.rsplit("/", 1)[1].split("-")[1])
        return chapter_page(n)


def test_lotm_1301_to_1305_selects_and_saves_all_five_chapters_including_1303(tmp_path):
    rm = IrregularRM(1432)
    logs: list[str] = []
    a = FreeWebNovelNetAdapter(request_manager=rm, log=logs.append)
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    job = ScrapeJob(novel_slug=LOTM.novel_slug, adapter_key="freewebnovel_net", start=1301, end=1305,
                    delay=0.0, output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=tmp_path / "out",
                    allocate_run=True, formats=("txt",))
    report = pipeline.run_scrape(job, adapter=a, request_manager=rm, log=logs.append,
                                 sleep_fn=lambda _s: None, manifest_store=store)
    assert report.effective_range == (1301, 1305) and not report.failed
    assert sorted(p.name for p in (report.output_dir / "TXT").glob("*.txt"))[2].startswith("Chapter 1303_")
    assert len(list((report.output_dir / "TXT").glob("*.txt"))) == 5
    assert f"{BASE}/lord-of-the-mysteries/chaspter-1303-1303-title-1303.html" in [u for u, _ in rm.fetched]
    assert sorted({int(u.split("page=")[1]) if "page=" in u else 1 for u, _ in rm.fetched
                   if ".html?page=" in u or u.endswith("mysteries.html")}) == [1, 32, 33, 34]
    assert sum("irregular address" in line for line in logs) == 1
    assert not any("not on the site's list" in line or "gap" in line.lower() for line in logs)
