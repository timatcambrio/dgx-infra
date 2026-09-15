# PROXY CORPUS

Why `dgx-infra` needs a proxy corpus, what that corpus must span, and where to source it.
Facts and reasoning only; the running record stays in `CONTINUITY.md`.

## [PREMISE]

- 2026-09-15 [USER] The deliverable is `dgx-infra`, the pipeline. The client stands up their
  own `dgx-knowledge` and populates it with their own internal documents. We never see that
  corpus, before or after delivery.
- 2026-09-15 [USER] The documents released to us (`diu-internal-docs/temp-holding`, 6 files)
  are restricted. They cannot be shared, redistributed, or committed, and neither can their
  converted markdown. No further documents are expected.

Two consequences, and they point the same way:

1. Acceptance cannot be "convert the corpus and read the output". There is no corpus to
   read. Acceptance is that the pipeline behaves defensibly on documents nobody here has
   seen, and reports honestly when it cannot.
2. The released six cannot serve as the test corpus even privately, because they cannot be
   committed. Every test the suite runs must be against synthetic fixtures or public
   documents. That is already the standing scope rule; the new fact is that it is now
   permanent rather than a precaution.

## [BIAS] The released sample is release-filtered

The six documents are not a random draw from the client's corpus. They are the subset that
cleared release review: final, cleaned-up, externally presentable. That filter correlates
with exactly the structural features the evidence profile measures, so the sample's profile
is a biased estimator of the population's.

Measured on the four released PDFs: `annot=0`, `w/CL=0`, `fields=0` on all four. Earlier
work read that as evidence the annotation-anchoring machinery is unused. Under the release
filter, the opposite reading is better supported: documents carrying reviewer annotations,
filled form fields, tracked changes and redactions are systematically *less* likely to clear
release, so their absence from the sample is close to uninformative about their frequency in
the corpus.

What the sample DOES transfer, because release review does not distort it:

- register and provenance — DoD acquisition, briefings, guidance, solution catalogues
- authoring toolchain — Office exports to PDF, slide decks exported to PDF
- the dominance of borderless tables over ruled ones (68/76, 41/56, 9/14, 4/10 pages)
- image-dominant pages as a routine occurrence, not an exception

What it does NOT transfer: the distribution of annotations, form fields, scans, and
redactions. Those must be assumed present and covered, not inferred absent.

- 2026-09-15 [DECISION] Do not treat `annot=0, fields=0` on the released sample as evidence
  that annotation and widget handling is dead weight. The sample is filtered on precisely
  that axis. Keep the machinery; cover it with proxies and fixtures.
- 2026-09-15 [DECISION] Proxy selection targets COVERAGE OF THE PLAUSIBLE POPULATION, not
  resemblance to the released six. Selecting proxies that match the sample's profile
  propagates the release filter into the test corpus and would leave the highest-risk
  regions untested. Supersedes the earlier "match the Bridge row" screening idea.

## [FINDING] Redaction collides with the boxed-text path

- 2026-09-15 [TOOL] Verified against a synthetic probe (throwaway, not committed): a filled
  rectangle drawn over page text is indistinguishable from a note box, because
  `_is_note_box` asks only that a rect be filled-or-stroked and under `PDF_BOXED_MAX_AREA`
  (0.25) of the page. Three behaviours, one of them bad:

  * **Proper redaction** (rect, underlying text removed from the content stream): the box
    holds no words, `_boxed_notes` skips it, nothing is emitted. Correct.
  * **Improper redaction** (black rect painted over text still in the content stream — the
    classic failure in released government PDFs): the hidden text is extracted, REMOVED from
    the body pool, and PROMOTED to its own `> **Boxed text:**` block. The probe emitted
    `> **Boxed text:** Vendor Alpha Systems Incorporated of Reston.` and left the visible
    remainder of that sentence as the dangling fragment `The awardee is`.
  * **Size is not a defence.** A 500x150pt block on a letter page is 15.5% of it, well under
    the 0.25 threshold, and was promoted whole.

  So on an improperly redacted document the pipeline does not merely fail to respect the
  redaction — it isolates the redacted text, sets it apart with emphasis, and damages the
  sentence that was left visible. For a defence client that is a disclosure concern as much
  as a conversion defect, and it is invisible to every existing test because no fixture
  carries a rect over live text.

- 2026-09-15 [ASSESSMENT] Not yet decided, and it is a real design question rather than an
  obvious fix: the pipeline's principle is to preserve evidence, not resolve it, and
  "text under an opaque filled rect" is a measurement it could faithfully emit. But emitting
  it is also the thing a redaction exists to prevent. Options, cheapest first:
  (a) a synthetic fixture pinning the current behaviour so it is at least known;
  (b) treat an opaque dark fill with text beneath it as a distinct kind and mark it
      (`> **Obscured text:**`), which reports rather than resolves and lets the client
      decide;
  (c) suppress the text and emit a marker naming only that obscured content was found;
  (d) a switch, defaulting to whichever of (b)/(c) the client asks for.
  This needs the client's call, not ours. Worth raising with them explicitly.

## [PLAN] What the proxy corpus must span

Selected by failure mode, not document type. The right-hand column is what the released
sample can and cannot vouch for.

| Region                          | Why it matters                              | In sample |
|---------------------------------|---------------------------------------------|-----------|
| Briefing decks, image-heavy     | Dominant released form; 12/14 pages @ 100%  | yes       |
| Policy prose + ruled tables     | Dominant released form                      | yes       |
| Borderless-table-dense prose    | Largest extraction surface in the corpus    | yes       |
| Tabular data (catalogue/CSV)    | Chunking destroys row/header binding        | yes       |
| **Annotated / redlined**        | Suppressed by the release filter            | NO        |
| **Filled forms (DD/SF series)** | Suppressed by the release filter            | NO        |
| **Scanned / faxed**             | `needs_ocr` is 0% in every corpus measured  | NO        |
| **Redacted**                    | Collides with the boxed-text path, above    | NO        |
| **.docx w/ tracked changes**    | The docx analogue of annotations            | NO        |

The five NO rows are where the residual risk sits.

## [SOURCE] Where to get them

FOIA reading rooms are the closest public analogue available: they are restricted internal
government documents that have been released, so they share the client's register,
provenance, authoring tools and structural pathologies — including scans, annotations and
redactions, which ordinary public-affairs PDFs lack.

- `open.defense.gov` — DoD FOIA reading room
- `governmentattic.org` — large and browsable; heavy on scans and internal memoranda
- `dtic.mil` — technical reports; dense ruled tables
- `diu.mil`, `acquisition.gov`, DAU — the client's own public register
- GAO and DoD IG reports — long prose with tables

Deck-shaped specimens:

    "all hands" OR "town hall" briefing slides filetype:pdf site:*.mil
    "industry day" briefing filetype:pdf site:*.gov

Screen candidates with `make profile` rather than by eye, and keep a candidate for the
region it covers, not for how closely its row resembles a released document.

## [PLAN] What shipping a pipeline changes about "done"

If the client runs this on documents we never see, three things stop being internal
scaffolding and become part of the product:

1. **The report is a deliverable.** EVIDENCE NOTES, INCOMPLETE markers, LOW-TEXT PAGES and
   the profile table are how someone who has never read this code discovers that their
   document converted badly. They must be legible to that person.
2. **Every threshold is a documented, configurable parameter, never a tuned constant.** Any
   number fitted to our proxy corpus is a landmine on theirs. Each ships with what it
   measures and how to tell it is wrong for a given corpus. `IMAGE_PAGE_COVERAGE` already
   follows this; the rest should.
3. **`pipeline answerability --cases PATH` is the client's acceptance test**, and the only
   one available to them. It needs a documented case-authoring workflow and a template, or
   it will not be used.

- 2026-09-15 [PLAN] Proposed, NOT approved: add `--anonymize` to `scripts/profile_corpus.py`
  (and the report) replacing filenames with a stable hash. The profile output is already
  content-free by test — counts and geometry only — so with filenames removed it is
  plausibly releasable even when the documents are not. That turns the client's real corpus
  from an invisible population into a measured one: they run `make triage && make report &&
  make profile` on their own machine and send back the tables. This is the single cheapest
  route to knowing the true distribution, and it is the only one that survives the access
  constraint.
