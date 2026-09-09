# Benchmarks

Every number the README leans on, with what produced it. Measured 2026-09-09
unless a paragraph says otherwise. The harness that runs them is
`scripts/bench_beir.py` for retrieval and `tests/test_retrieval_check.py` for
the acceptance check on a corpus of your own.

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

```bash
uv run scripts/bench_beir.py                    # downloads SciFact and NFCorpus once into bench/
uv run scripts/bench_beir.py --arm hybrid       # the dense leg, with whatever SILICA_EMBEDDING_* names
uv run scripts/bench_beir.py --compare 'scifact-zg-full-*' 'scifact-hybrid-sections-potion-*'
# n=300  diff nDCG@10 = -0.0034  95% CI [-0.0186, +0.0121]  -> noise
```

Inside Silica the dense leg is worth +0.014 and +0.017 with the static
model, +0.052 and +0.038 with nomic. Measured and dropped: one vector per
document instead of per section (0.721 on SciFact, where a document *is* one
abstract, at 1,391 ms a query against 239 ms; on real documents it returns
the introduction, not the passage), and Snowball stemming (+0.002 and
+0.005, MRR@10 down on the second). Query latency is not tabulated because
zvec-grep ran in `direct` mode, one process and one model load per query.

## What the harness measurements say

Leave the choice to the harness. Measured with Opus on 2026-09-09, 144 runs,
every run correct: no arm of any experiment separated on the answer, so what
moves is the work spent reaching it. Twelve questions over a 5.5M-token
corpus of papers, Silica reached for spontaneously, against the same harness
without the plugin: 3.9 turns instead of 5.0, 2.9 tool calls instead of 4.0,
$0.20 instead of $0.22 a task at a warm cache. The same answers for a
quarter fewer calls. On code the model never reaches for `silica_search`,
because the questions name identifiers and the descriptions say grep wins
there: the two resident schemas cost 13% on those tasks and return nothing.
Told to use the tools instead of grep it is worse everywhere: 49% over
spontaneous use on the papers, 1.7 times a plain grep on code, 3.2 times at
the worst task.

Put the pair in front of the model instead: `silica mcp` marks
`silica_search` and `silica_read` `anthropic/alwaysLoad`, so a client that
defers MCP tools behind its own tool search keeps those two in the list and
the other three ride behind them. On the same twelve document tasks the model
reached for a deferred search once, and a resident one twelve times. The
marker is declared, never inferred, and it has to reach the wire as `_meta`:
a wrapper that rebuilds the tool list carries it over itself.
