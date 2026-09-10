# Benchmarks

Every number the README leans on, with what produced it. Measured 2026-09-09
unless a paragraph says otherwise. The harnesses are `scripts/bench_beir.py`
for retrieval, `scripts/bench_code.py` for code search, `scripts/baseline.py`
and `scripts/judge.py` for the agentic runs, and
`tests/test_retrieval_check.py` for the acceptance check on a corpus of your
own.

## How search says no, in numbers

Measured on 254 converted papers (22 MB): the answered questions scored
0.69 to 1.00 on their top hit, a question the corpus does not cover scored
0.44. No boolean is derivable from lexical signals alone, so Silica exposes
the numbers and the harness decides to stop, read, or rephrase.

The same column, read down the hits, says whether one document or several
are in contention. Best section per document on that corpus: `0.99 · 0.67 ·
0.60` is one paper, `0.97 · 0.94 · 0.67` is two, `0.79 · 0.73 · 0.72` is
spread, `0.44 · 0.29 · 0.28` is nothing. Only the absolute level tells the
first shape from the last, so Silica reports the column and no ratio between
its neighbours: a ratio scores the first and the last shape alike.

## What the dense leg buys

On the 254-paper corpus: 8,835 sections embed in 161 s, and a paraphrase
with none of the papers' rare words, "should the unit that gets indexed be a
passage or the whole document when the texts are long", goes from the fourth
document to the first, landing on that paper's section 5 rather than its
abstract (`tests/test_retrieval_check.py`). The same question in Italian
stays unanswered with that English model: another language needs a
multilingual embedder, not a bigger one.

## Retrieval benchmark

`scripts/bench_beir.py` scores the document ranking on two BEIR corpora of
paper abstracts with document-level judgements, SciFact (5,183 documents,
300 queries) and NFCorpus (3,633, 323), written as one `.md` per document,
the same folder handed to every arm.

**Lexical only, no model**

| Arm | nDCG@10: SciFact · NFCorpus |
|---|---|
| **Silica**, BM25 over documents, then over their sections | **0.662 · 0.311** |
| zvec-grep 0.2.2, `--fts` | 0.649 · 0.297 |
| ck 0.7.11, `--lex` | 0.630 · 0.289 |

BEIR's published BM25 baselines are 0.665 and 0.325. Only the first row is a
model-free index: `--fts` and `--lex` are served from one their tool builds
with embeddings in it.

**The same embedder on both sides**, `potion-retrieval-32M`, static, no server

| Arm | nDCG@10: SciFact · NFCorpus |
|---|---|
| **Silica**, hybrid, the `[dense]` extra | **0.675 · 0.328** |
| zvec-grep 0.2.2, hybrid, RRF over its lexical and vector arms | 0.672 · 0.330 |

**The best embedder each was measured on**

| Arm | nDCG@10: SciFact · NFCorpus |
|---|---|
| **Silica**, hybrid, `nomic-embed-text` through Ollama | **0.713 · 0.349** |
| ck 0.7.11, `--sem`, `bge-small` | 0.706 · 0.327 |
| ck 0.7.11, hybrid, `bge-small` | 0.668 · 0.319 |

Paired per query with a 95% bootstrap interval, the whole second block is a
tie, and so are zvec-grep's `--fts` on SciFact and ck's `--sem` on SciFact;
every other gap clears zero. So the second block is the one that matters:
the same vectors rank the same, and Silica gets there in 13k lines of Python
with no daemon, no vector store and no bundled model. ck's fusion sits below
its own dense arm on both corpora, the cost of a keyword arm and a score
threshold in front of it. zvec-grep has no row in the third block: its
transformer models run through onnxruntime, whose CUDA provider wants a
cuDNN this machine lacks, and on CPU `local/nomic-embed-text-v1.5` took
25 GB before the kernel killed it.

Inside Silica the dense leg is worth +0.052 and +0.038 with nomic, both
clear of zero. With the static model it is +0.017 on NFCorpus, clear, and
+0.014 on SciFact, inside the interval (-0.010 to +0.037), so potion is
shown to help on one corpus of the two.

**What the fusion adds to the embedder**, measured 2026-09-10 with
`--arm dense`, the same nomic vectors ranked alone, best section per
document, no BM25 and no RRF:

| Arm | nDCG@10: SciFact · NFCorpus |
|---|---|
| BM25 | 0.662 · 0.311 |
| nomic alone | 0.702 · 0.347 |
| BM25 + nomic, RRF | 0.709 · 0.351 |

Paired, the hybrid is +0.007 and +0.003 over nomic alone, inside the
interval on these abstracts, and it wins more queries than it loses (64/44
and 104/88). The lexical leg is what serves `terms_absent` and an exact
identifier, which no embedder does.

Configuration measured and left as it is: one vector per document instead of
per section (0.721 on SciFact, where a document *is* one abstract, at
1,391 ms a query against 239 ms, and on real documents it returns the
introduction rather than the passage), Snowball stemming (+0.002 and +0.005,
MRR@10 down on the second), and nomic's task prefixes (measured 2026-09-10:
`search_document: ` on every section and `search_query: ` on the query, as
its model card requires, -0.004 on SciFact and +0.002 on NFCorpus, both
inside the interval; the knobs stay, `SILICA_EMBEDDING_DOC_PREFIX` and
`SILICA_EMBEDDING_QUERY_PREFIX`, empty by default, and the README's example
sets them because the card asks for them). Query latency is not tabulated
because zvec-grep ran in `direct` mode, one process and one model load per
query.

```bash
uv run scripts/bench_beir.py                    # downloads SciFact and NFCorpus once into bench/
uv run scripts/bench_beir.py --arm hybrid       # the dense leg, with whatever SILICA_EMBEDDING_* names
uv run scripts/bench_beir.py --compare 'scifact-zg-full-*' 'scifact-hybrid-sections-potion-*'
# n=300  diff nDCG@10 = -0.0034  95% CI [-0.0186, +0.0121]  -> noise
```

## Code search against zvec-grep

Measured 2026-09-10, `scripts/bench_code.py`: the question as the query, the
ranked hits against the code the reference answer rests on. Two task sets.
The 20 SWE-QA questions zvec-grep publishes for its own benchmark, at the
commits it pins, eleven repositories from requests (62 files) to sqlfluff
(4,904); the evidence is the files and symbols the judge's reference answer
names, annotated by four Sonnet agents from those references and checked
mechanically (every recorded line defines its symbol, 138 symbols). And the
twelve local questions of `scripts/baseline.py`, evidence lines located by
grep. Both sides embed with the same model, potion-retrieval-32M, static,
in-process for Silica and `local/potion-retrieval-32m` for zvec-grep 0.2.2.
Silica's index here is the code index (`SILICA_INDEX_CODE`): one unit per
function, method, class or constant, the module residue between them,
80-line windows where no parser applies, BM25 over the units and one vector
per unit, fused by rank; the identifier tokenizer that came with it (the
whole name and its words) left the BEIR abstracts where they were, 0.662 and
0.311.

A hit locates a symbol when its anchor line falls inside the symbol, or its
range overlaps the symbol and is not more than three times longer, so a
whole-class hit earns nothing for a method inside it. `chars` is what the
caller receives for ten hits, the hits' JSON or zg's agent markdown with its
short preview.

**The 20 SWE-QA questions**, k = 10

| Arm | file hit@5 · @10 | file MRR | symbol hit@10 | symbol recall | chars |
|---|---|---|---|---|---|
| **Silica, hybrid** | **0.85 · 0.90** | **0.68** | **0.75** | **0.24** | 8,266 |
| Silica, lexical | 0.65 · 0.75 | 0.47 | 0.45 | 0.14 | 8,194 |
| Silica, vectors alone | 0.80 · 0.85 | 0.67 | 0.65 | 0.21 | 6,225 |
| zvec-grep, hybrid | 0.65 · 0.75 | 0.54 | 0.55 | 0.17 | 7,326 |
| zvec-grep, `--vector` | 0.60 · 0.80 | 0.61 | 0.55 | 0.18 | 6,821 |
| zvec-grep, `--fts` | 0.45 · 0.60 | 0.36 | 0.45 | 0.11 | 6,690 |

**The 12 local questions**

| Arm | file hit@5 · @10 | file MRR | symbol hit@10 | symbol recall |
|---|---|---|---|---|
| **Silica, hybrid** | **0.75** · 0.83 | **0.45** | **0.75** | **0.68** |
| Silica, lexical | 0.67 · 0.83 | 0.43 | 0.67 | 0.53 |
| zvec-grep, hybrid | 0.67 · 0.83 | 0.43 | 0.67 | 0.57 |
| zvec-grep, `--vector` | 0.58 · 0.75 | 0.32 | 0.50 | 0.50 |
| zvec-grep, `--fts` | 0.67 · 0.75 | 0.36 | 0.50 | 0.43 |

Paired per question, Silica hybrid against zvec-grep hybrid: on the 20
SWE-QA questions file MRR +0.135 with a 95% interval of +0.010 to +0.271,
eight wins and two losses, and file recall, symbol hit and symbol recall
clear of zero too; on all 32 the sign holds and the intervals touch zero
(file MRR +0.091, -0.001 to +0.186), because on the local twelve the two
hybrids tie. Against `--vector`, zvec-grep's best mode here, symbol hit
+0.219 (+0.062 to +0.375) and the rest noise. Inside Silica the fusion is
worth file MRR +0.135 and symbol hit +0.219 over the lexical leg, both
clear; against the vectors alone it is neutral on SWE-QA and decisive on
the local set (0.45 against 0.31), so no fusion weight was tuned.
Indexing, from nothing to lexical index plus vectors: sympy 34 s + 31 s
against `zg index` 79 s, django 28 + 26 against 71, at under 280 MB. Query
time is not comparable: zg's process loads its model per query in `direct`
mode. Raw runs, gold and provenance are kept under `bench/swe-qa/`.

**The ask that puts the search first**, `scripts/baseline.py`, arm S on the
20 SWE-QA questions with Sonnet, one repetition, judged blind against
zvec-grep's reference answers by `scripts/judge.py`. Three texts carry the
ask: `silica_search`'s description opens with it and names what not to do
("for a question that names no identifier, call this before Grep, Read or
Glob; do not grep for the words of a question"); a `UserPromptSubmit` hook
in the plugin adds one line before a question that names no identifier,
asking the model to say whether the search applies and blocking nothing
(16 of the 20 questions qualify); and the block `silica setup claude` writes
between markers into `~/.claude/CLAUDE.md`. With the ask the search ran in
17 of 20 runs (27 calls, 6 reads) at 4.7 turns and 24 s a question, judge
59.7; the same plugin without it searched in none, at 6.5 turns and 28 s,
judge 50.6. Paired, turns -1.75 (-3.05 to -0.55) and seconds -4.5 clear of
zero, cost +$0.018 (-0.005 to +0.041) and judge +9.1 (-8.2 to +25.7) inside
the interval at one repetition, 13 wins and 6 losses. Against the same
morning's grep-and-reads arm the judge is +15.4 (+3.3 to +28.6). In the same
grid zvec-grep's MCP search, with the guidance `zg install` writes, was
chosen once in its 20 runs.

## What the harness measurements say

Leave the choice to the harness. Measured with Opus on 2026-09-09, 144 runs,
every run correct: no arm of any experiment separated on the answer, so what
moves is the work spent reaching it. Twelve questions over a 5.5M-token
corpus of papers, Silica reached for spontaneously, against the same harness
without the plugin: 3.9 turns instead of 5.0, 2.9 tool calls instead of 4.0,
$0.20 instead of $0.22 a task at a warm cache. The same answers for a
quarter fewer calls. The code questions in that set all named identifiers,
where the descriptions say grep wins and the model greps; what a question
that names none does with a code index on is the section above.

Put the pair in front of the model instead: `silica mcp` marks
`silica_search` and `silica_read` `anthropic/alwaysLoad`, so a client that
defers MCP tools behind its own tool search keeps those two in the list and
the other three ride behind them. On the same twelve document tasks the model
reached for a deferred search once, and a resident one twelve times. The
marker is declared, never inferred, and it has to reach the wire as `_meta`:
a wrapper that rebuilds the tool list carries it over itself.

**What `code_pack` adds to grep**, measured 2026-09-10 with Haiku 4.5, the
same runner (`scripts/baseline.py`), the four code tasks on this repository
and the eight on competitors' checkouts (168 to 4,386 files), three
repetitions, 72 runs, no timeout. The "before" arm is grep and file reads
with the plugin disabled. The "after" arm keeps grep and makes
`silica_code_pack` the only reader: `Read`, `cat`, `sed`, `head`, `tail`,
subagents and the other Silica tools are refused, and the prompt orders the
two steps, locate with grep, read with the pack. It measures what the
structure the code graph keeps is worth once grep has found the symbol, not
whether a model picks it.

| | grep + read | grep → `code_pack` |
|---|---|---|
| this repository, C1–C4, correct | 11 of 12 | 12 of 12 |
| cost at a warm cache | $0.048 | $0.057 |
| turns · tool calls · seconds | 5.0 · 4.0 · 19 | 6.2 · 5.2 · 24 |
| competitors' repositories, H1–H8, correct | 18 of 24 | 18 of 24 |
| cost at a warm cache | $0.037 | $0.037 |
| turns · tool calls · seconds | 4.9 · 3.9 · 19 | 6.3 · 5.3 · 23 |

No task separated the arms on the answer. Where the answer is one symbol's
body in a file not worth reading whole the pack halves the cost: H5, two
constants in two files, $0.067 to $0.034; H8, a twelve-line label function,
$0.054 to $0.033. Where grep output already was the answer the extra call
shows up as cost instead: C2, call sites as `path:line`, $0.012 to $0.027.

**The same four tasks with Sonnet**, the ones Haiku failed (C1, H3, H4, H6),
`claude-sonnet-5`, both arms, three repetitions, 24 runs. Correct by the
rubric: 10 of 12 with grep and reads, 11 of 12 with the pack; cost at a warm
cache $0.140 and $0.146; turns 6.6 and 8.1, seconds 23 and 35. Sonnet entered
every repository and called the pack in all twelve runs of its arm. On C1 the
pack is a third cheaper with Sonnet ($0.200 to $0.134, 6.0 turns against
7.3), where the stronger model finds the symbol inside the pack that the grep
arm hunts across files. No separation on correctness with either model.
