# ADR-032: Document parsers are small libraries run in a subprocess, not a conversion framework

Status: accepted (builds on ADR-029; the evidence is in [the parser spike](../product/spikes/2026-11-12-document-parsers.md))

Implements Phase 2 of the knowledge-base design in `docs/superpowers/specs/2026-11-12-knowledge-base-design.md` (sections 6 and 8). Requirements FR-1, FR-2, FR-3 and FR-6.

## Context
Phase 2 imports PDF, DOCX, PPTX, XLSX, HTML, Markdown and text files as Sources made of Sections and Fragments. Every Fragment carries a locator the reader can check against the original: a page, a slide, a character span. A parser is therefore useful for NODO only if it keeps structure (headings, pages, slides) and fails loudly on a bad file. A parser that returns plausible text from a broken file is worse than one that raises, because the text would be cited.

The spike measured the candidates on a CPU laptop. The 50-page text PDF (about 145,000 characters) took 1.5 to 2.6 s with pdfminer.six and 0.1 to 0.2 s with pypdfium2, against 8.6 s for MarkItDown. Docling did not run on PDF: its layout and table models need a download nobody has approved, and its install was 1.4 GB. On the other formats the direct libraries recovered all the headings on the fixtures, and the framework candidates did no better.

## Decision
One library per format, each behind the same `DocumentParser` port:

| Format | Default | Fallback | Locator |
|---|---|---|---|
| PDF | pypdfium2 page gate, then pdfminer.six text (1.5 to 2.6 s per 50 pages) | pypdf | Page and characters; headings from the outline, else ranked by font size |
| DOCX | python-docx | none | Characters; page only where explicit break markers exist |
| PPTX | python-pptx | none | Slide and characters; one Section per slide, with the notes in the same Section |
| XLSX | openpyxl, read-only, `data_only=True` | none | One Section per sheet, 50-row blocks with the header row repeated, and characters; `Locator` does not change |
| HTML | beautifulsoup4 with `html.parser` | none | Characters; script, style, nav, footer, aside and hidden elements are dropped |
| Markdown | markdown-it-py | none | Characters; front matter is kept as a block |
| TXT | standard library decode: utf-8-sig, UTF-16 with BOM, then cp1252 | none | Characters |

The PDF page numbers are for a 50-page text PDF measured on an 8-core CPU; a 4-core laptop is expected to take 2 to 3 times longer.

**Parsers run in a subprocess, never in the engine process.** A hostile or broken file can exhaust memory, loop, or crash a native library (pypdfium2 bundles PDFium). The engine starts a worker per file with a size limit, an entry and expansion-ratio limit for ZIP-based formats, and a time limit, and treats a non-zero exit, a timeout or an oversized output as a failed import with a stable error code. Every import of these libraries is lazy and happens inside the worker.

**The parser id and version are stored with each Source version.** The spike found the stable text hash identical across runs but liable to change when a library is upgraded. Recording which parser produced a version lets a re-import tell a changed file from a changed parser, and lets a later upgrade re-derive on purpose.

**`Locator` stays as it is.** A spreadsheet sheet is a Section, not a new locator field. The 50-row blocks keep the header row so a Fragment still reads as a table fragment. A cell-level locator is not added; the cost is that a citation points to a block of rows and characters, not to one cell.

**PDF without a text layer is reported, not guessed.** A page with fewer than 10 non-space characters is flagged. A document where every page is flagged is imported as "no text layer" with its own error code. OCR is out of scope for this phase.

**The dependencies are an optional extra.** `pip install ".[document]"` adds the eight parsers (about 60 to 70 MB with their dependencies). `reportlab` is in the same extra but is used only by the tests to generate PDF fixtures. The default install and the Azure image do not need any of them.

## Alternatives rejected
- **MarkItDown.** It converts everything to Markdown with one call, but the conversion loses what the locators need: PDF headings come out flat and the page marks are form feeds. It also fails silently on the cases that matter: a corrupt DOCX returns the garbage as text, cp1252 text is misread ("Caffč ŕ Milano"), and an XLSX formula with no cached value becomes the string `NaN`. It is also slower (8.6 s for the 50-page PDF) and heavier (156 MB peak, plus about 220 MB of dependencies).
- **Docling.** PDF was not measured, because its layout models need a download that has not been approved and the install is 1455 MB. On the other formats it took 4 s and 340 MB per file with structure no better than the direct libraries, and no character offsets. Re-evaluating it for PDF tables is a later task, to be done when the download is approved and with real files.
- **Unstructured.** Paper only. Its core pulls in numba, spacy and more, high-resolution PDF needs layout models, poppler and Tesseract, and it returns elements with page numbers but no character offsets. It stays the second choice if the direct stack and Docling both fail.
- **pdfplumber as the default PDF path.** It adds table blocks but took 5 to 6 s and 510 MB for 50 pages. It stays an optional later addition for table-heavy PDFs.
- **PyMuPDF.** It is AGPL-3.0 (or a commercial licence). NODO is MIT, so it must not be imported. It may be present in a developer environment from another project; a test pins that no NODO module imports `fitz` or `pymupdf`.
- **Parsing in the engine process.** Faster to write, but one bad file could take down transcription and everything else the engine does.

## Consequences
- Eight small libraries replace a framework. Each failure is explicit: encrypted or truncated PDFs, corrupt Office files and unreadable encodings raise, and the import fails with a code instead of storing garbage.
- Structure quality varies by format. DOCX has no real page numbers, so its locator is the section path and characters. PDF headings by font size are a heuristic that failed on a uniform-font document and was tested only on generated files. The outline is preferred and PDF headings are proposals to review until about 10 real files have been annotated.
- A prompt-injection sentence in a document comes out as an ordinary paragraph from every parser. The defence is not in the parsers: document text reaches a model only as delimited data and its output is validated.
- Real files will be slower than the fixtures (an estimated 2 to 5 times). The worker's time limit and the queue (FR-3) absorb that; a long import must not starve a transcription.
- Each library is a licence and a supply-chain item. They are listed in `docs/LICENSING.md` and `NOTICE`. All are MIT, BSD or Apache-2.0 or a combination. The ones with bundled binaries (pypdfium2 with PDFium, lxml, pillow) are the ones to re-check on every upgrade.
- Not covered by the spike, so not decided here: OCR, multi-column and figure-heavy PDFs, files saved by Word or Excel, charts and SmartArt, password-protected Office files, embedding and extraction quality.
