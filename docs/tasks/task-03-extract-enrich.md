# Task 03 — Extract & Enrich stages

**Read `AGENTS.md` first. It is binding.** Read the README and the code from Tasks 01–02 before planning. Work on branch `task-03`.

## 1. Goal

Turn every fetched raw file (`data/<site_id>/raw/<url_id>.html|.pdf`) into a **clean, structured document**, then run a corpus-level enrichment pass.

```
raw → extract → clean/<url_id>.json + pages.clean_hash → enrich → duplicates, metadata
```

The output feeds the next task, chunking, which cuts by section and attaches heading paths to every chunk. **Structure and citation data matter more than anything else.**

**Out of scope:** chunking, embeddings, indexing, OCR, near-duplicate detection, fixing reversed Hebrew text, any LLM use, any network access.

## 2. Decisions (final)

1. **HTML main content.**
   - Use the site-profile selectors: `content_selectors` (where the content is) and `remove_selectors` (what to drop).
   - If the content selectors match nothing, fall back to `trafilatura`, and record `extra.extraction_method = "fallback"` on the page.
2. **Boilerplate** is removed through `remove_selectors` in the profile. The enrich stage also writes a **report of the most frequent text blocks** across all clean documents (block text hash, % of pages, a sample), so the selectors can be tuned. There is no automatic removal.
3. **Clean document = structured JSON** (frozen dataclasses in `domain/`, serialized to JSON):
   - document: `title`, `breadcrumbs`, `canonical_url`, `language`, and `sections`;
   - each section: `heading_path` (a list of headings, from the page title down) and `blocks`;
   - each block: `kind` (`paragraph` | `list_item` | `table_row` | `qa`), `text`, and an optional `question` (for `qa`) and `page_number` (for PDF).
4. **FAQ / accordions:** every question + answer pair is **one `qa` block**, never split. FAQ markup is identified by profile selectors.
5. **Tables:** one `table_row` block per row, written as `"<column header>: <value>; …"`.
6. **PDF** with PyMuPDF:
   - one section per page, with the document title as heading and `page_number` kept on every block;
   - **no OCR**: a PDF with almost no text becomes `skipped` with reason `scanned_pdf`;
   - **reversed Hebrew** is detected heuristically and only reported, not fixed. Heuristic: Hebrew final letters (ך ם ן ף ץ) appearing at the start of words above a configured ratio.
7. **Enrich is a separate, corpus-level stage** that runs after extract on every run. It is idempotent and cheap, and it has no per-page receipt.
8. **Duplicates:** exact matches only, by `clean_hash`, among active extracted pages.
   - The representative is the page whose `canonical_url` points to itself or to a group member; otherwise the lowest `discovery_depth`; on a tie, the shortest URL.
   - The other pages become `mark_duplicate(of=representative)`.
   - A former duplicate whose hash no longer matches its group is restored, with a `restored` event.
9. **Thin content:** fewer than `min_text_chars` characters after cleaning (the profile setting) means `skipped` with reason `thin_content`.
10. **Hebrew normalization** happens once, in extract, as a pure function in `ingest/`: Unicode NFC, unified quotes and gershayim, invisible bidi marks removed, whitespace collapsed. Word-level search normalization does **not** belong here.

Extract stage mechanics, using the existing infrastructure:
- Pages are selected with `iter_needing_stage("extract")` (input: `raw_hash`, version from settings).
- The extractor is chosen by `content_type`.
- `clean_hash` = SHA-256 of a deterministic serialization of the **text content** (title + headings + blocks), not of the metadata.
- Events: `changed` if `clean_hash` differs from the stored one, `unchanged` if it is the same, even when the raw bytes changed. This is the whole point of the two hashes.
- Failures go through the failure policy with stage `extract`. `skipped` pages leave the index through the existing unindex path.

## 3. Architecture (high level)

| Location | Content |
|---|---|
| `domain/clean_document.py` | `CleanDocument`, `Section`, `Block`, `BlockKind`: frozen dataclasses and enum, plus JSON (de)serialization |
| `ports/extractor.py` | `Extractor` Protocol: `supports(content_type)`, and `extract(raw_bytes, page, rules)` returning a document or a skip reason |
| `adapters/extract/html_extractor.py` | lxml + profile selectors + trafilatura fallback |
| `adapters/extract/pdf_extractor.py` | PyMuPDF |
| `ingest/hebrew.py` | pure normalization functions |
| `ingest/reversed_text.py` | pure heuristic |
| `storage/clean_store.py` | read/write `clean/<url_id>.json` (concrete class, no interface) |
| `pipeline/stages/extract.py` | the extract stage |
| `pipeline/stages/enrich.py` | the enrich stage |

- Register both stages and set `pipeline.stages: [discover, fetch, extract, enrich]`.
- Profile additions go under `extraction.html` (content / remove / title / breadcrumb / FAQ selectors) and `extraction.pdf` (scanned-text threshold, reversed-text ratio). `min_text_chars` also goes in the profile. Everything is validated with pydantic.
- Expected: **no schema migration** (the existing columns and `extra` should suffice). If you find one is needed, propose it.
- New CLI debug command: `python -m siterag show --site fnx --url <url>` prints a page's clean document in a readable form. The developer will use it to check pages by hand.

## 4. Step 1 is a checkpoint: propose the selectors

The full crawl has already run, and its raw files are in `data/fnx/raw/`.
1. Sample about **20 raw HTML files** across different `page_type` and `domain` values.
2. Inspect their DOM, then propose `content_selectors`, `remove_selectors`, title, breadcrumb and FAQ selectors, with a short justification for each.
3. Show a readable extraction preview of **3 pages**: one product, one claims and one FAQ page.
4. **Stop and wait for approval** before continuing.

Working on the local raw files is allowed. **There is no network access in this task.**

## 5. Tests (risk-based, per AGENTS.md §11.6)

- Hebrew normalization: a parametrized table.
- HTML extractor on small **synthetic** fixtures that mimic the site structure. Cover:
  - heading paths;
  - remove selectors applied;
  - a FAQ pair kept as one `qa` block;
  - table rows formatted;
  - fallback used when the selectors miss;
  - thin content skipped.
- PDF extractor on tiny PDFs generated in the test: a text page with `page_number`, a scanned-like PDF skipped, and the reversed-text heuristic on a table of examples.
- `clean_hash` is deterministic, and it ignores metadata-only changes.
- Extract stage: new, `unchanged` when raw bytes change but clean text does not, version bump, failure, and `skipped`.
- Enrich stage: representative selection (canonical, then depth, then URL length), duplicate restored, and idempotent.

## 6. Done

Follow the AGENTS.md Definition of Done. In addition:
- run `extract` and `enrich` on the real local raw files;
- report: counts per status and skip reason, how many pages used the fallback, the number of duplicate groups, the top 10 boilerplate blocks, and the PDFs flagged as reversed.
