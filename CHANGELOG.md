# Changelog

All notable changes to the analysis scripts in this repository.

Dates are the date the change was published here. Where a change altered a
previously published benchmark figure, the old and new values are both listed,
because the point of this repository is that the measurements are auditable.

---

## 2026-10-05

### Added — `runqc.py` (new)

A one-command pipeline that runs every check on a finished book in order and
writes everything needed to log the run. Previously these were four manual
steps with four opportunities to mislabel a file.

```
python runqc.py <run_id> "<book folder>"
```

It runs `chaptertimes.ps1`, audits for cloud-fallback chapters, assembles the
narrative body from the individual chapter files, runs `bookdiff repetition
--chapters-only`, and writes a dated QC checklist, a matrix row, an append-only
run ledger, and per-chapter detail rows. See the README for full usage.

### Added — `bookdiff.py`: motif saturation

A new report section measuring recurring **images** rather than recurring
sentences: one- to four-word phrases that recur across the whole book.

The existing 10-word-window measure is blind to this by design. `"golden
thread"` appearing 110 times in 72,000 words shares no 10-word window with
itself and is not a four-word habit, so neither existing number saw it — but a
reader meets it every other page and calls the book repetitive. Three separate
AI judges flagged motif repetition in books this script was scoring at 0.3%.

The important column is **`chapters`**, not the count. A phrase in 29 of 30
chapters is the model's voice and will be in every book it writes. The same
count inside two chapters is a runaway passage, which is a different problem
with a different fix. Rows concentrated in a small number of chapters get a
guaranteed `CONCENTRATED` sub-list, because ranking on count alone buries them
behind ordinary vocabulary.

Design decisions worth knowing:

- Words inside a repeated 10-word window are **excluded**, so this section
  measures the repetition the headline number cannot see instead of restating
  it. On a clean book that removes almost nothing; on a looping one it removes
  the loop and leaves the style tics visible. The report prints how much it
  skipped.
- Singular and plural are merged **here and nowhere else** in the report.
- Frequent everyday words are filtered out of single-word rows. Ranking on raw
  count alone puts `chair` above `mirror`, because mirrors are rare in English
  and chairs are not. `--motif-all` disables the filter.
- Names and places are detected and listed separately, because a protagonist's
  name recurs for ordinary reasons.
- `--motif-rate PER10K` sets the floor (default `1.5`, about once every 6,700
  words).

### Added — `bookdiff.py`: frame-break detection

A new check for prose where the narrator stops telling the story and starts
describing the document it is inside:

```
  1263  chapter ref       ...the architectural dive she had performed in Chapter 18.
  1495  meta narration    The chapter ended not with the silence of a closed door...
   765  production note   Word count: 2,450
   835  model voice       I hope this helps clarify the structure of the sanctuary.
   941  addresses reader  The reader will notice that she never explained herself.
```

A novel should not know it has chapter numbers. These are generation defects
rather than style, no quality score catches them, and they are free to find.
Chapter headings and everything from the first back-matter heading onward are
excluded, so a hit is prose. Line numbers are real file lines.

Measured precision on 23 hits across five real manuscripts: **95%**, after
fixing three false-positive classes (see below). The remaining class is a
character legitimately reading a chapter of a book, which this check cannot
distinguish and which the report tells you to read for.

### Added — `bookdiff.py`: recurring short phrases

A four-word-phrase section with per-10,000-word rates, so books of different
lengths are comparable. A model can score under 1% on the headline number and
still say the same four words every few hundred words.

### Added — `bookdiff.py`: `--count PHRASE`

Repeatable. Counts an exact phrase and reports its rate per 10,000 words. Built
for checking a claim before believing it — a beta reader, human or otherwise,
can be confidently wrong about frequency.

### Fixed — `bookdiff.py`: chapter detection on files with mixed heading shapes

The splitter took the **first** pattern matching three or more lines. A single
export often mixes heading shapes (`## Chapter 5: Title`, `# Chapter 5`,
`Chapter 5`, `**Chapter 5 - Title**`), so a file using two shapes was split on
whichever appeared first and the remaining chapters were silently swallowed
into their predecessors. On a 30-chapter test file using four shapes it found
**15**.

Replaced with one tolerant pattern tried first, which matches all four shapes
once Markdown emphasis has been normalized away.

**Effect on published figures:** the headline `Internal repetition` percentage
is computed over the whole body and did not change on any file where chapter
one's heading was already being detected — verified identical (2.2% before and
after) on a mixed-heading test file. What does change on such files is the
chapter count, the drift table, and anything derived from them. If you are
citing a drift or per-chapter figure produced before this date, re-run it.

### Fixed — `bookdiff.py`: back matter counted as chapters

`--chapters-only` now truncates the raw text at the first back-matter heading
**before** splitting into chapters. Per-chapter endnote entries carry their own
`Chapter N` headings, so a splitter run over the whole export detected them as
chapters and they survived any trim applied afterwards — a 30-chapter book
reported 60 chapters.

**Effect on published figures** (all moved up, as the mechanism predicts):

| Run | Before | After |
| --- | --- | --- |
| A1 | 45.4% | 45.5% |
| B1 | 74.6% | 75.2% |
| B2 | 83.7% | 86.0% |

### Fixed — `bookdiff.py`: three frame-break false-positive classes

- `"the next chapter of her life"` and variants. Ordinary English, not the
  narrator describing the document. Two of the first 23 field hits.
- A bare mention of `"language model"`. Fires constantly when the manuscript is
  *about* AI development, which the test corpus is. Narrowed to the
  first-person giveaways (`"as an AI"`, `"as a language model"`).
- A bare `TODO`. A developer character writing TODOs in her own code is
  in-story.

Precision on the field set went from 78% to 95% with no loss of real hits.

### Fixed — `bookdiff.py`: assistant voice in the manuscript

Two classes found by an AI judge that the first version of the check missed —
not the narrator breaking frame, but the assistant answering a prompt, printed
into the book as prose:

- `"Since there was no preceding text provided…"` opening a chapter
- `"I will begin a new narrative."`

Both are now detected. The `I will …` pattern is anchored to line start so it
does not fire on dialogue (`"I will begin again tomorrow," she said`).

### Fixed — `bookdiff.py`: names detected as common nouns

A character who almost always opens their sentence leaves no mid-sentence
capitalization to detect, so they were listed as a common-noun motif. A token
never once seen lower-case now counts as a name regardless.

### Changed — `bookdiff.py`: motif rates measured against the whole body

Rate and floor are computed against total body words, never against what
survived the repeat-mask. The per-10k column exists so books of different
lengths can be compared; a denominator that shrank with how loopy the book is
would have made exactly that comparison meaningless.

---

## Known issues

- **`chaptertimes.ps1` truncation flag has four documented false-positive
  classes**: a trailing emoji, a one-word final sentence (`"Ready."`), a
  back-matter file, and a chapter ending in `**bold**`. A practical rule from
  the benchmark data: a real truncation happens at the model's output cap, and
  only a runaway chapter gets near it — so a truncation flag on a
  normal-length chapter is almost always a formatting artifact, and one on a
  runaway chapter is almost always real.
- **`runqc.py`'s `slowest_chapter_sec`** includes chapter one's interval, which
  can be a meaningless large number (the gap since the folder was created). If
  that figure exceeds the run's wall clock, it is chapter one's artifact.
- **`runqc.py`'s fallback audit fails in both directions under retries.** It
  flags chapters running far above the run's median rate, on the theory that a
  suspiciously fast chapter was generated in the cloud. In a run full of
  retries the median is dragged down and the *honest* chapters look like the
  anomalies — one real run produced eight false positives this way. It also
  missed a genuine cloud-generated chapter whose retries made it come out
  slower than the median. Engine labels are the only reliable check.
- **`bookdiff.py` frame-break detection cannot tell a model slip from a
  character legitimately mentioning a chapter of a book.** Read the hits.
- **`--chapters-only` treats `Epilogue` as back matter.** If your epilogue is
  part of the narrative, omit the flag or edit `BACK_MATTER`.
