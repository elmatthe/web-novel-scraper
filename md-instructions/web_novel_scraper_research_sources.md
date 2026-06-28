# Web Novel Scraper Research Sources

**Prepared for:** Claude Code and Codex  
**Project context:** Existing web-novel scraping application, including FreeWebNovel support  
**Research date:** June 24, 2026

---

## Purpose

Use the sources in this document as research material while improving the existing scraper.

Do not replace working project code merely because another project uses a different structure. First inspect the current repository, identify a specific weakness or missing feature, and then use these sources to compare possible solutions.

Priority order:

1. Reuse sound architectural ideas.
2. Adapt small implementation patterns only when the source license permits it.
3. Preserve the current user-facing workflow and output formats unless the task specifically requires a change.
4. Add tests for every selector, parser, pagination rule, cache rule, or output change.
5. Treat all website selectors as unstable and validate them against current HTML fixtures.

---

## Source Summary

| Priority | Source | Best Use |
|---|---|---|
| High | `jmarioste/scraper-freewebnovel` | FreeWebNovel-specific selectors, URL patterns, parser separation, and fixture-based tests |
| High | `web-novel-scraper` on PyPI/GitHub | Multi-site architecture, configurable decoders, caching, request management, CLI organization, and EPUB exporting |
| Medium | FMHY Web Scraping / Crawling index | Discovery of additional tools when the current stack has a clearly defined gap |
| Medium | `lorien/awesome-web-scraping` | Comparing established Python/JavaScript libraries and reviewing scraping fundamentals |

---

# 1. jmarioste/scraper-freewebnovel

- Repository: https://github.com/jmarioste/scraper-freewebnovel
- Language: TypeScript
- Package style: Small site-specific library
- License: MIT
- Main dependencies: Axios and Cheerio
- Main target: `freewebnovel.com`

## What It Does

This repository separates FreeWebNovel scraping into focused modules for:

- Novel metadata
- Chapter-list pagination
- Chapter-page extraction
- Genre discovery
- Slug discovery
- Shared URL and metadata helpers
- HTTP loading
- Data models
- Tests using saved/mock HTML

The public API includes functions for parsing a novel page, obtaining chapter lists, parsing chapter pages, extracting chapter IDs, and discovering genres or novel slugs.

## Most Relevant Files

- `src/novel-page.ts`
  - Builds the novel URL from a slug.
  - Extracts Open Graph novel metadata.
  - Extracts title, alternate titles, author, genres, type, status, cover image, rating, description, and latest chapter details.

- `src/chapter-list.ts`
  - Detects the number of index pages.
  - Builds paginated chapter-list URLs.
  - Combines chapter entries from all pages.

- `src/chapter-page.ts`
  - Extracts the chapter title and paragraph text.
  - Excludes selected non-content elements.

- `src/helpers.ts`
  - Provides reusable metadata and chapter-ID extraction helpers.

- `src/wrapper.ts`
  - Centralizes HTTP retrieval and HTML loading.

- `test/scraper.test.ts`
  - Uses mocked HTTP responses and stored HTML fixtures.
  - Tests novel metadata, chapter lists, chapter pages, genres, and slug discovery.

## Useful Ideas for the Current Project

1. **Separate metadata, TOC, and chapter parsing.**  
   Avoid one oversized scraper function. Each page type should have a focused parser.

2. **Use metadata before fragile visible-page selectors.**  
   The repository extracts many values from `meta[property="..."]` fields. Metadata can be more stable than layout-specific nested selectors.

3. **Centralize URL construction.**  
   Keep FreeWebNovel URL patterns in one adapter instead of scattering string templates across the application.

4. **Normalize chapter identifiers.**  
   Parse the chapter ID from the URL once and store it in the normalized chapter model.

5. **Test using saved HTML fixtures.**  
   Network-free parser tests are faster and make selector regressions easier to diagnose.

6. **Keep pagination logic separate from chapter extraction.**  
   A TOC paginator should discover pages; a TOC parser should extract chapter records from each page.

## Limitations and Risks

- The project is small and should not be treated as a complete production crawler.
- Its dependencies and URL assumptions may be dated.
- Current FreeWebNovel markup may differ from the saved selectors.
- The request wrapper shown in the project is minimal and does not demonstrate robust timeouts, retries, backoff, caching, or structured error handling.
- Some fields use the current time instead of extracting a genuine publication date.
- The chapter-ID regular expression assumes one specific URL format.

## Instructions for Claude Code / Codex

- Compare current project selectors with:
  - `div.m-newest2 ul.ul-list5 li a`
  - `div.m-read div.txt > p`
  - Open Graph novel metadata fields
  - `#indexselect` pagination
- Do not copy selectors without validating current site HTML.
- Port ideas into the project’s existing language and architecture rather than introducing TypeScript solely for this source.
- Reuse code only with the required MIT copyright/license notice.
- Prefer improving the current request layer rather than copying the repository’s minimal Axios wrapper.

---

# 2. web-novel-scraper on PyPI

- PyPI: https://pypi.org/project/web-novel-scraper/
- Repository: https://github.com/ImagineBrkr/web-novel-scraper
- Documentation: https://web-novel-scraper.readthedocs.io/stable/
- Latest version reviewed: `2.11.3`
- Release date reviewed: June 20, 2026
- Language: Python
- Python requirement: Python 3.10 or newer
- Distribution: CLI/package
- Output focus: EPUB
- Supported sources listed by the project include FreeWebNovel and several other novel sites.

## What It Does

This is a broader multi-site application that organizes scraping around:

- A novel record
- One or more tables of contents
- Chapter records
- Host-specific decoder rules
- Local chapter storage
- Request management
- Configuration management
- Logging
- Output exporters
- Tests
- CLI commands

Its documented workflow creates a novel, syncs its TOC, requests missing chapters, and exports the result to EPUB.

## Most Valuable Architectural Idea: Configurable Decoders

The project defines host-specific extraction rules using decoder configuration. A decoder can describe:

- Host/domain
- Whether the TOC is paginated
- Chapter-index selector
- Next-page selector
- Chapter-title selector
- Chapter-content selector
- Whether matches return one element or an array
- Whether to extract text or an attribute such as `href` or `title`

This is highly relevant to a multi-site scraper. It allows site differences to live in adapter/configuration files rather than in the core crawl pipeline.

## Other Useful Ideas

1. **TOC as the source of truth.**  
   Generate chapter records from the table of contents instead of guessing chapter ranges.

2. **Persistent chapter caching.**  
   Once a chapter has been successfully saved, avoid downloading it again unless refresh is requested.

3. **Clear pipeline stages.**
   - Create/load novel
   - Sync TOC
   - Determine missing chapters
   - Request chapters
   - Parse/clean content
   - Export

4. **Normalized models.**  
   Keep source-specific HTML details out of the main novel/chapter data structures.

5. **Dedicated request manager.**  
   Networking should be isolated from parsing and exporting.

6. **Configurable storage paths and logging.**  
   The package supports environment-based data directory and logging configuration.

7. **Exporter separation.**  
   EPUB generation is handled separately from scraping, making additional TXT, Markdown, JSON, or HTML exporters easier to maintain.

8. **Host validation tests.**  
   Site adapters should have focused tests that verify their required selectors and expected extraction behavior.

## Relevant Package Structure

The repository contains modules or folders for:

- `decode.py`
- `decode_guide/`
- `novel_scraper.py`
- `request_manager.py`
- `models.py`
- `file_manager.py`
- `config_manager.py`
- `logger_manager.py`
- `exporters/`
- `custom_processor/`
- `tests/hosts_validation/`
- Exporter, decoder, configuration, request, and I/O tests

## Dependencies Worth Noting

The reviewed package metadata lists:

- `requests`
- `bs4`
- `ebooklib`
- `click`
- `platformdirs`
- `dataclasses_json`
- `python-dotenv`

These choices support a conventional Python CLI with HTML parsing, local application storage, serialization, and EPUB output.

## Limitations and Risks

- No explicit software license was visible in the reviewed repository root or `pyproject.toml`.
- Until a valid license is confirmed, treat its source code as **reference-only**.
- Do not copy or adapt substantial code from this project without confirming permission.
- Some optional behavior references FlareSolverr. Do not add anti-bot bypass infrastructure merely because another project supports it.
- Prefer respecting site rules, conservative request rates, and normal HTTP/browser behavior.
- A large external architecture should not be transplanted wholesale into a smaller working application.

## Instructions for Claude Code / Codex

Study this project primarily for:

- Decoder/adapter configuration design
- TOC synchronization
- Incremental chapter caching
- Request-manager boundaries
- Local data-directory organization
- Logging and recoverable errors
- Exporter interfaces
- Host-validation tests

Before adopting a pattern:

1. Identify the matching deficiency in the current repository.
2. Write a small design note describing the proposed adaptation.
3. Preserve existing working output formats and GUI/CLI behavior.
4. Implement the smallest useful version.
5. Add regression tests.
6. Do not copy source code until its license is verified.

---

# 3. FMHY Internet Tools — Web Scraping / Crawling

- Page: https://fmhy.net/internet-tools#web-scraping-crawling
- Type: Curated directory/index
- Best role: Tool discovery, not implementation authority

## What It Contains

The reviewed section links to scraping and crawling resources such as:

- Awesome Web Scraping
- Web Scraping FYI
- Browser-based data scrapers
- Spider/crawler applications
- Heritrix
- Scrapling
- Crawl4AI
- Archival crawlers and website-download tools

## How It Can Help

Use this page only when the current project has a specific unresolved need, such as:

- Static HTML parsing is no longer sufficient.
- JavaScript rendering is required.
- A crawl queue is needed.
- Content extraction needs to support additional site structures.
- Archival or reproducible page snapshots are needed.
- The project needs a maintained alternative to an obsolete dependency.

## Recommended Evaluation Process

For every candidate found through FMHY, verify:

1. Official repository or documentation
2. License
3. Recent maintenance activity
4. Supported Python/Node versions
5. Security history
6. Test coverage
7. Packaging quality
8. Whether it solves an actual current requirement
9. Whether it adds unnecessary browser/runtime complexity
10. Whether a lighter existing dependency already solves the problem

## Suggested Candidates to Investigate Only When Needed

- **Scrapling:** Potential modern scraper/parsing option.
- **Crawl4AI:** Potential structured or LLM-oriented extraction option.
- **Heritrix:** More relevant to large archival crawling than a normal web-novel downloader.
- **Web Scraping FYI:** General reference material.
- **Awesome Web Scraping:** Broad library comparison.

## Limitations and Risks

- FMHY is an index, not a guarantee of quality, safety, legality, maintenance, or compatibility.
- Listings can mix lightweight libraries, hosted services, browser extensions, and large crawling systems.
- Do not add tools simply because they appear in the list.
- Avoid captcha-solving, proxy-marketplace, fingerprint-bypass, or aggressive anti-bot tooling unless there is an explicitly approved and lawful requirement.
- Do not send novel content or user data to third-party scraping services without review.

## Instructions for Claude Code / Codex

- Use FMHY for discovery after a concrete technical gap is documented.
- Shortlist no more than three candidates for any one gap.
- Compare candidates against the current stack in a small decision table.
- Prefer maintained, open-source, locally executable tools.
- Do not change dependencies until compatibility and licensing are confirmed.

---

# 4. lorien/awesome-web-scraping

- Repository: https://github.com/lorien/awesome-web-scraping
- Type: Curated list of libraries, tools, APIs, and manuals
- License for the list: CC BY 4.0
- Organization: Separate Python, JavaScript, CLI, language-specific, and learning-resource files

## What It Contains

The repository organizes scraping resources by language and function.

The Python list includes categories for:

- HTTP/network clients
- Scraping frameworks
- HTML/XML parsers
- Browser automation
- Asynchronous processing
- Queues and concurrency
- Text processing
- Structured data
- Crawling and extraction tools

The JavaScript list includes:

- HTTP clients such as Axios
- Crawling frameworks such as Crawlee
- HTML parsing such as Cheerio
- Browser automation through Playwright and Puppeteer
- Text and content extraction tools

The manuals section emphasizes understanding core web concepts including:

- HTTP
- HTML and DOM
- CSS selectors
- XPath
- URLs
- DNS
- TLS
- Text encoding
- Concurrency

## Most Relevant Options for This Project

### For a Python scraper

- `requests` or `httpx` for normal HTTP retrieval
- `BeautifulSoup` or `lxml` for static HTML parsing
- `aiohttp` or `httpx` asynchronous mode only when concurrency is justified
- `Scrapy` only if crawl scale, queues, middleware, and pipelines justify a framework
- `Playwright` only for pages that genuinely require browser rendering
- `Crawlee` as a possible higher-level crawler if queueing and browser/static modes are both required

### For a TypeScript/Node scraper

- Axios or native `fetch` for HTTP retrieval
- Cheerio for static HTML
- Crawlee for crawling, queues, storage, and optional browser integration
- Playwright for JavaScript-rendered pages
- Puppeteer only when Chrome-specific automation is preferred

## How It Should Be Used

Use the repository to compare categories, not to install many packages.

A sensible selection process is:

1. Try normal HTTP and a static parser.
2. Add retries, timeouts, rate limiting, and caching.
3. Use browser automation only when the required data is unavailable in the returned HTML or an accessible data endpoint.
4. Adopt a full crawling framework only when the current hand-built queue and pipeline are becoming difficult to maintain.
5. Verify each candidate through its own official documentation and repository.

## Limitations and Risks

- Curated lists can contain obsolete or lightly maintained projects.
- A listing is not an endorsement.
- Descriptions may be brief or outdated.
- Each linked project has its own license.
- The CC BY 4.0 license applies to the curated list, not automatically to every linked tool.
- Avoid dependency churn and architecture rewrites based only on popularity.

## Instructions for Claude Code / Codex

- Use `python.md`, `javascript.md`, `cli.md`, and `manuals.md` as indexes.
- Verify all shortlisted dependencies at their official source.
- Prefer the project’s existing language and dependencies unless a measurable problem requires change.
- Document why a new dependency is necessary and what existing code it replaces.
- Add a small proof-of-concept or benchmark before adopting a major framework.

---

# Cross-Source Recommendations for the Current Scraper

The strongest combined design is:

## 1. Site Adapter Layer

Create one adapter per supported domain.

Each adapter should define:

- Accepted hostnames
- Novel metadata extraction
- TOC discovery
- Pagination behavior
- Chapter URL extraction
- Chapter title extraction
- Chapter body extraction
- Site-specific cleanup rules
- URL normalization
- Optional date/author/cover extraction

## 2. Declarative Selector Configuration

Where practical, store selectors and extraction rules in configuration rather than hard-coding every site into the core engine.

Configuration should support:

- CSS selector
- Single or multiple matches
- Text or attribute extraction
- Required/optional fields
- Pagination selector
- Content exclusions
- Fallback selectors
- Host aliases

Keep custom Python/TypeScript processors available for sites that cannot be represented cleanly through configuration alone.

## 3. Normalized Models

Use stable internal objects such as:

### Novel

- `title`
- `slug`
- `source_url`
- `source_host`
- `author`
- `description`
- `cover_url`
- `status`
- `genres`
- `chapters`

### Chapter

- `source_url`
- `source_id`
- `number`
- `title`
- `content`
- `published_at`
- `downloaded_at`
- `content_hash`
- `status`

## 4. Reliable Request Manager

Centralize:

- User agent
- Timeouts
- Retry policy
- Exponential backoff
- Conservative per-host delay
- Redirect handling
- HTTP status validation
- Encoding detection
- Optional session/cookie support
- Cache lookup
- Structured request errors
- Cancellation support

Do not place networking logic inside HTML parser functions.

## 5. Incremental Cache and Resume

Store successful results so interrupted jobs can resume.

Useful cache metadata:

- URL
- Retrieval timestamp
- HTTP status
- Content hash
- Parser version
- Adapter version
- Output filepath
- Last error
- Retry count

Allow explicit refresh without redownloading everything by default.

## 6. TOC-First Discovery

Treat the table of contents as the primary chapter source.

The TOC pipeline should:

1. Load the first TOC page.
2. Detect pagination.
3. Visit each required page conservatively.
4. Normalize chapter URLs.
5. Remove duplicates.
6. Preserve intended order.
7. Compare against cached chapters.
8. Download only missing or explicitly refreshed chapters.

## 7. Content Cleaning

Keep generic and site-specific cleaning separate.

Generic cleanup may include:

- Whitespace normalization
- Empty paragraph removal
- HTML entity decoding
- Safe line-break handling
- Duplicate-title removal
- Encoding cleanup

Site-specific cleanup may include:

- Advertisement blocks
- Navigation text
- Translator notes, when configured
- Repeated headers/footers
- Embedded recommendation blocks

Never remove text through broad rules without fixture-based tests.

## 8. Fixture-Based Testing

For every supported site, store sanitized HTML fixtures for:

- Novel page
- Single-page TOC
- Paginated TOC
- Normal chapter
- Chapter with unusual markup
- Missing metadata
- Changed/fallback selector case
- Error or empty-content case

Tests should run without live network access.

Add a separate optional live validation command that checks a small number of pages without becoming part of the normal unit test suite.

## 9. Exporter Layer

Keep scraping separate from output generation.

Possible exporters:

- Plain TXT
- Chapter-separated TXT
- Markdown
- JSON
- EPUB
- HTML

All exporters should consume the same normalized novel/chapter models.

## 10. Observability and Error Reports

Provide:

- Human-readable progress
- Debug logging
- Per-chapter failure records
- Final success/skip/failure counts
- Resume instructions
- A machine-readable run report

A single bad chapter should not destroy an otherwise recoverable download job.

---

# Required Workflow for the Coding Agents

When using this research:

1. Read the current repository instructions and architecture first.
2. Identify the exact task currently assigned.
3. Map the task to one or more ideas in this report.
4. Inspect the original source directly before relying on this summary.
5. Confirm license and maintenance status.
6. Propose the smallest compatible change.
7. Preserve current working behavior.
8. Implement with clear module boundaries.
9. Add or update offline fixtures and tests.
10. Run the relevant test suite.
11. Document:
    - Source consulted
    - Idea adapted
    - Code copied, if any
    - License/attribution requirements
    - Files changed
    - Tests run
    - Remaining risks

---

# Licensing Rules

- **`jmarioste/scraper-freewebnovel`:** MIT. Code may be reused or adapted only while preserving the required copyright and permission notice.
- **`ImagineBrkr/web-novel-scraper`:** No explicit license was visible in the reviewed repository metadata. Treat code as reference-only until a license or direct permission is confirmed.
- **FMHY:** It is primarily an index. Check the license of every linked project separately.
- **`lorien/awesome-web-scraping`:** The list is CC BY 4.0. Linked projects retain their own licenses.

Never assume that publicly visible source code is automatically reusable.

---

# Final Recommendation

For the current project:

1. Start with the FreeWebNovel repository to validate domain-specific URL and selector assumptions.
2. Use the PyPI project to improve adapter configuration, caching, request separation, and exporter boundaries.
3. Use Awesome Web Scraping to compare libraries only when the existing stack has a documented limitation.
4. Use FMHY only as a secondary discovery index.
5. Prefer incremental improvements over a framework rewrite.
6. Keep the scraper polite, resumable, testable, and easy to repair when a site changes.
