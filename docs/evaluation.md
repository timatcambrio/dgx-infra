# Evaluation

Three checks, each answering a different question:

| Check | Question | Needs |
| --- | --- | --- |
| `pipeline answerability` | Did the text needed to answer a question survive conversion? | `kb/` only |
| `kb eval` | Does search rank the right section near the top? | an index |
| `scripts/mcp_probe.py` | Can an assistant reach that text through the MCP tools, with a correct citation? | an index |

None of them uses a model to grade answers. To check a running deployment over the network
(TLS, credentials, revocation), use `scripts/http_probe.py`, described in
[Administration](administration.md#checking-a-deployment-end-to-end).

## Checking the output can still answer questions

Comparing output byte for byte catches change. It cannot catch output that is stable and
useless, like a budget grid flattened into prose, or a note stranded from the field it
describes. Both would pass a file comparison forever.

You can write questions with known answers and check that the text needed to answer them
survived conversion:

```yaml
cases:
  - id: submission-type-first-attempt
    question: Which box do I check for the first submission attempt?
    document: linked-form.md
    expect:
      - "[points to: TypeOfSubmission]: Use Application for the first submission attempt."
```

```bash
uv run pipeline answerability --cases /path/to/your/cases.yaml
```

This reads `kb/` and nothing else, so it needs neither the source documents nor `SOURCE_DIR`,
and it exits non-zero on failure so it can gate a release. No model is involved: the cases are
literal substring checks, which keeps them offline, repeatable, free, and open to argument by
anyone who thinks a case is wrong.

Write `expect` as the shortest string that makes the answer findable.

## `kb eval`

```bash
uv run kb eval [--cases eval/retrieval.yaml] [--k 5]
```

Runs a fixed set of questions with known answers (`eval/retrieval.yaml`) against the index
and prints a table:

```
case id                         lexical   vector    fused
form-token-exact                      1        5        1
...
lexical hit@5: 100%
vector hit@5: 50%
fused hit@5: 100%
```

Each row is one case; the number is the rank (1 = top result) of the first section that
leg returned matching the case's expected document (and, if the case specifies them, an
expected phrase or page) within the top `--k`. A `-` means that leg never found it. The
three percentages at the bottom are hit@k across all cases, one per leg.

The committed cases run only against the synthetic fixtures in
`tests/retrieval/fixtures/kb/` and are checked in the test suite (fused hit@5 must be
100% there). They say nothing about retrieval quality on an actual document collection.

To measure retrieval on your own documents, write a cases file in the same format against
your index, check it with
[`scripts/check_eval_cases.py`](#checking-a-cases-file-before-you-trust-it-scriptscheck_eval_casespy),
and record the three percentages as your floor. Keep the cases file outside the repository
if its questions describe the documents. A change to chunking, the embedding model or fusion
is then measured against that floor and must not lower it. Do not tune settings toward the
cases; fix whatever the evidence points to (conversion, sectioning or chunking) and
re-record the floor afterwards.

How to read the legs: the lexical leg ANDs every non-stop word of the question, so a natural
question containing one word the right chunk lacks scores zero there. It is meant for exact
tokens (a section number, a form number, a phone number, a zip code). Expect tables and CSVs
to retrieve less well than prose: a short table of numbers under a good heading is still
hard to reach, because rows of bare figures embed far from a natural-language question.

**Run a current embedding server.** With ollama 0.21.0, vector hit@5 was 28 points lower
than with 0.35.1 or 0.40.0 on the same documents and cases, and nothing else showed it: the
lexical leg was identical, single embeddings matched to five decimal places, and `kb index`
reported no errors. The cause is **UNCONFIRMED**; the leading candidate is how old versions
handle several texts in one request, which `embed.py` sends in batches.

### Checking a cases file before you trust it: `scripts/check_eval_cases.py`

```bash
uv run python scripts/check_eval_cases.py --cases /path/to/cases.yaml
```

`kb eval` scores a case as a miss when *any* of `expected_slug`, `expected_page` and
`expected_phrase` fails, and all three must be true of the **same** section. Two very
different things therefore look identical in the table: retrieval missed, or the case asked
for something no single section can satisfy. This script separates them. It reports a case
as unsatisfiable when the page or phrase is absent, and specifically flags the
**over-constrained** case where each constraint is met alone but no one section meets both.
Such a case would make the file test the sectioniser rather than the retriever. It also
warns when a phrase appears in more than three sections (weak evidence) and when a page is
split across more than six sections, which usually means a table was flattened. Read-only;
exits 1 if any case is unsatisfiable.

### Probing the MCP contract: `scripts/mcp_probe.py`

```bash
uv run python scripts/mcp_probe.py --cases /path/to/mcp-cases.yaml
```

`kb eval` checks whether the right section *ranks*. This checks whether the evidence is
**reachable through the MCP tools, with a correct citation**. It launches `kb serve
--transport stdio` as a subprocess, the same way an assistant application does, and calls
the tools. Per case it checks that all five tools are advertised, that `search` returns the
expected document, that `fetch` resolves and returns more than the 300-character snippet,
that the expected phrase is present in the *fetched* text (reachable, not merely indexed),
that the citation names the source file and the url sits under `KB_URL_BASE`, and that
`get_section(neighbours=1)` is a superset of `fetch`.

It deliberately does **not** judge whether an assistant's prose answer is correct or whether
it called `fetch` before quoting: those are properties of the assistant, not of this server,
and grading them automatically would need an LLM judge. Cases have a `requires:` list of
such behaviours and the probe prints them as a checklist; a case marked `expect_damage: true`
prints the fetched text so you can confirm the damage is visible to a reader at all. The
server's own call log goes to `mcp-probe-server.log` (`--server-log` to change it) rather
than interleaving with the report.
