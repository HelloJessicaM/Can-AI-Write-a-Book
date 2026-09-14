#!/usr/bin/env python3
"""
bookdiff.py - Measure how much of a manuscript is still the AI's words,
              and how much a book repeats itself.

Python 3.8+. No libraries to install. No internet. Nothing leaves your computer.

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------

  Compare an edited manuscript against the AI draft it came from:

      python bookdiff.py compare ai_draft.txt my_edited_book.txt

  Measure how much a single book repeats itself:

      python bookdiff.py repetition my_book.txt

  Save a report instead of printing it:

      python bookdiff.py compare ai_draft.txt edited.txt --out report.txt

  Measure the narrative only, with front and back matter removed:

      python bookdiff.py repetition my_book.txt --chapters-only

  Check a phrase a beta reader claims is overused:

      python bookdiff.py repetition my_book.txt --count "soft glow" --count "the pull to stay"

  If your chapters aren't detected, tell it what they look like:

      python bookdiff.py repetition book.txt --chapter-regex "^CHAPTER"

--------------------------------------------------------------------------
WHAT THE NUMBERS MEAN
--------------------------------------------------------------------------

CARRYOVER  - the share of your manuscript that is still word-for-word the
             AI's. Counted two ways: whole sentences that are identical, and
             10-word windows that appear in the AI draft. The window number
             runs a few points higher because it catches half-edited
             sentences that the sentence count misses.

REPETITION - the share of 10-word windows in a book that appear more than
             once. Long AI generations tend to loop. This number is most
             useful as a comparison, not against a fixed threshold: run it
             on a book you trust to set your own baseline, then compare.
             Deliberate refrains and repeated liturgy will show up here too,
             so read the phrase list before concluding anything.

SHORT TICS - REPETITION uses a 10-word window, so it is blind to the
             two-, three- and four-word habits that readers actually
             notice. The short-phrase section catches those and reports
             them as a rate per 10,000 words, so books of different
             lengths can be compared. A model can score low on REPETITION
             and still say "soft glow" every thousand words.

DRIFT      - how much a late chapter overlaps chapter one. Overlap that
             climbs toward the end means the model started recycling.

--------------------------------------------------------------------------
MIT licensed. Do what you like with it.
--------------------------------------------------------------------------
"""

import argparse
import re
import sys
import unicodedata
from collections import Counter

WINDOW = 10

BACK_MATTER = re.compile(
    r'^#{0,6}\s*(epilogue|afterword|endnotes|glossary|appendix|appendices|'
    r'further reading|discussion guide|acknowledg|about the author|'
    r'a note to readers|reader thanks|bibliograph|index)\b', re.I)

DEFAULT_CHAPTER_PATTERNS = [
    r'^#{1,6}\s*Chapter\s+\d+',
    r'^Chapter\s+\d+',
    r'^CHAPTER\s+[\dIVXLC]+',
    r'^#{1,6}\s+\d+\s*$',
]


def read(path):
    for enc in ('utf-8', 'utf-8-sig', 'cp1252', 'latin-1'):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
    sys.exit(f"Could not read {path} as text. Save it as plain .txt or .md first.")


def normalize(text):
    """Make curly quotes, dashes and markdown invisible to the comparison."""
    text = unicodedata.normalize('NFKC', text)
    for a, b in [('\u2019', "'"), ('\u2018', "'"), ('\u201c', '"'),
                 ('\u201d', '"'), ('\u2014', '--'), ('\u2013', '-'),
                 ('\xa0', ' '), ('\u2026', '...')]:
        text = text.replace(a, b)
    text = re.sub(r'[*_`]', '', text)
    text = re.sub(r'^[-=_*]{3,}$', ' ', text, flags=re.M)
    return text


def words(text):
    return [k for k, _, _ in tokens(text)]


def tokens(text):
    """(match_key, start, end) over the normalised text, punctuation preserved."""
    flat = re.sub(r'\s+', ' ', normalize(text))
    out = []
    for m in re.finditer(r"[A-Za-z0-9\u2019']+", flat):
        key = m.group().lower().replace('\u2019', "'")
        out.append((key, m.start(), m.end()))
    return out


def surface(text, start_tok, n=WINDOW):
    """The literal text of an n-gram, exactly as it appears (searchable)."""
    flat = re.sub(r'\s+', ' ', normalize(text))
    toks = tokens(text)
    return flat[toks[start_tok][1]:toks[start_tok + n - 1][2]]


def sentences(text):
    text = re.sub(r'\s+', ' ', normalize(text))
    parts = re.split(r'(?<=[.!?"])\s+(?=[A-Z"\'])', text)
    return [p.strip() for p in parts if len(p.split()) >= 4]


def sent_key(s):
    return ' '.join(re.findall(r"[a-z0-9']+", s.lower()))


def ngrams(word_list, n=WINDOW):
    return [tuple(word_list[i:i + n]) for i in range(len(word_list) - n + 1)]


def chapter_marks(lines, custom=None):
    """(line indices of chapter headings, pattern used). Needs 3+ to accept."""
    for pat in ([custom] if custom else DEFAULT_CHAPTER_PATTERNS):
        rx = re.compile(pat)
        marks = [i for i, ln in enumerate(lines) if rx.match(ln.strip())]
        if len(marks) >= 3:
            return marks, pat
    return [], None


def split_chapters(text, custom=None):
    """Prefer real markdown headings; fall back to bare 'Chapter N' lines."""
    lines = normalize(text).split('\n')
    patterns = [custom] if custom else DEFAULT_CHAPTER_PATTERNS
    for pat in patterns:
        rx = re.compile(pat)
        marks = [i for i, ln in enumerate(lines) if rx.match(ln.strip())]
        if len(marks) >= 3:
            out = []
            for j, start in enumerate(marks):
                end = marks[j + 1] if j + 1 < len(marks) else len(lines)
                out.append((lines[start].strip().lstrip('#').strip()[:60],
                            '\n'.join(lines[start + 1:end])))
            return out
    return [('(whole file)', text)]


FUNCTION_WORDS = set("""a an and as at be been but by did do for from had has have
he her hers him his i if in is it its me my not of on or she so that the their them
then there they this to up was we were what when which who will with you your it's
had been would could should there's""".split())


def body_only(raw, custom=None):
    """Front and back matter removed: the narrative and nothing else.

    Truncating the raw text BEFORE splitting is what makes this correct.
    Per-chapter endnote entries carry their own 'Chapter N' headings, so a
    splitter run over the whole export detects them as chapters and they
    survive any trim applied inside chapters.

    Returns (body_text, dropped_front_words, dropped_back_words).
    """
    lines = raw.split('\n')
    marks, _ = chapter_marks(lines, custom)
    if not marks:
        return raw, 0, 0
    start = marks[0]
    end = next((i for i in range(start, len(lines))
                if BACK_MATTER.match(lines[i].strip())), len(lines))
    body = '\n'.join(lines[start:end]).strip() + '\n'
    return (body,
            len(words('\n'.join(lines[:start]))),
            len(words('\n'.join(lines[end:]))))


def has_back_matter(raw):
    return any(BACK_MATTER.match(ln.strip()) for ln in raw.split('\n'))


def overlaps(a, b, n, k):
    """True if the two n-grams share k+ consecutive tokens (same passage)."""
    for off in range(-(n - k), n - k + 1):
        x = a[max(0, off):n + min(0, off)]
        y = b[max(0, -off):n + min(0, -off)]
        if len(x) >= k and x == y:
            return True
    return False


def pct(x):
    return f"{x * 100:.1f}%"


def emit(lines, out):
    text = '\n'.join(lines)
    if out:
        with open(out, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print(f"Report written to {out}")
    else:
        print(text)


def compare(ai_path, edited_path, chapter_regex, out):
    ai_raw, ed_raw = read(ai_path), read(edited_path)
    ai_chs = split_chapters(ai_raw, chapter_regex)
    ed_chs = split_chapters(ed_raw, chapter_regex)

    L = ["=" * 72,
         "CARRYOVER REPORT",
         f"  AI draft : {ai_path}   ({len(words(ai_raw)):,} words)",
         f"  Edited   : {edited_path}   ({len(words(ed_raw)):,} words)",
         "=" * 72]

    paired = min(len(ai_chs), len(ed_chs))
    if paired > 1 and len(ai_chs) != len(ed_chs):
        L.append(f"\nNote: {len(ai_chs)} chapters in the draft, {len(ed_chs)} in "
                 f"the edit. Comparing the first {paired} in order.")

    if paired > 1:
        L.append(f"\n{'ch':>4} {'words':>8} {'sentences':>10} {'windows':>9}   status")
        L.append("-" * 72)

    tot_s = tot_id = 0
    ai_all_ng = set(ngrams(words(ai_raw)))

    for i in range(paired):
        ai_body, ed_body = ai_chs[i][1], ed_chs[i][1]
        ai_keys = set(sent_key(s) for s in sentences(ai_body))
        ed_sents = sentences(ed_body)
        if not ed_sents:
            continue
        ident = sum(1 for s in ed_sents if sent_key(s) in ai_keys)
        s_rate = ident / len(ed_sents)

        ed_ng = set(ngrams(words(ed_body)))
        w_rate = len(ed_ng & ai_all_ng) / len(ed_ng) if ed_ng else 0

        tot_s += len(ed_sents)
        tot_id += ident

        if paired > 1:
            status = ("mostly AI" if s_rate >= .75 else
                      "heavily edited" if s_rate >= .60 else
                      "part rewritten" if s_rate >= .45 else
                      "mostly yours")
            L.append(f"{i+1:>4} {len(ed_body.split()):>8,} {pct(s_rate):>10} "
                     f"{pct(w_rate):>9}   {status}")

    ed_ng_all = set(ngrams(words(ed_raw)))
    overall_w = len(ed_ng_all & ai_all_ng) / len(ed_ng_all) if ed_ng_all else 0
    overall_s = tot_id / tot_s if tot_s else 0

    L += ["\n" + "=" * 72,
          f"  {pct(overall_s)} of sentences are word-for-word the AI's "
          f"({tot_id:,} of {tot_s:,})",
          f"  {pct(overall_w)} of 10-word windows appear in the AI draft",
          "=" * 72,
          "",
          "Cutting individual words and phrases barely moves these numbers.",
          "What moves them is writing new sentences."]
    emit(L, out)


def repetition(path, chapter_regex, out, chapters_only=False, count_phrases=None):
    raw = read(path)
    note = None
    if chapters_only:
        raw, front, back = body_only(raw, chapter_regex)
        if front or back:
            note = (f"  Front/back matter removed: {front:,} words before "
                    f"chapter 1, {back:,} words after the last chapter.")
        else:
            note = "  --chapters-only: nothing to remove, file is already body text."
    elif has_back_matter(raw):
        note = ("  NOTE: this file contains back matter and you did not pass "
                "--chapters-only.\n  Back matter is less repetitive than the "
                "novel, so this number reads low.")

    chs = split_chapters(raw, chapter_regex)

    L = ["=" * 72,
         "REPETITION REPORT",
         f"  {path}   ({len(words(raw)):,} words, {len(chs)} chapters)",
         "=" * 72]
    if note:
        L.append("")
        L.append(note)

    all_ng = ngrams(words(raw))
    counts = Counter(all_ng)
    repeated = sum(c for g, c in counts.items() if c > 1)
    rep_rate = repeated / len(all_ng) if all_ng else 0

    L.append(f"\n  Internal repetition: {pct(rep_rate)} of 10-word windows "
             f"appear more than once")
    L.append(f"  (compare against your own baseline; check the phrase list below first)")

    # report distinct repeated passages, not the same run at every offset
    first_pos = {}
    for i, g in enumerate(all_ng):
        if g not in first_pos:
            first_pos[g] = i

    picked = []
    for c, g in sorted(((c, g) for g, c in counts.items() if c > 2), reverse=True):
        if any(overlaps(g, p, WINDOW, 5) for _, p in picked):
            continue
        picked.append((c, g))
        if len(picked) == 10:
            break

    if picked:
        flat = re.sub(r'\s+', ' ', normalize(raw))
        L.append("\n  Most-repeated passages (distinct):")
        L.append(f"  {'windows':>9} {'find':>7}   passage")
        L.append("  " + "-" * 68)
        for c, g in picked:
            s = surface(raw, first_pos[g])
            hits = flat.count(s)          # non-overlapping, same as Ctrl-F
            L.append(f"  {c:>9} {hits:>7}   {s}")
        L.append("")
        L.append("  'windows' counts every overlapping 10-word position, so it measures")
        L.append("  how much of the text the passage occupies. 'find' is what a text")
        L.append("  search returns. For one unbroken repeating run the first is roughly")
        L.append("  ten times the second; a smaller ratio means the run is broken up.")

    # short tics: the 10-word window cannot see them, readers can
    N4 = 4
    total_w = len(words(raw))
    g4 = ngrams(words(raw), N4)
    positions = {}
    for i, g in enumerate(g4):
        positions.setdefault(g, []).append(i)

    # suppress phrases that sit inside a phrase already reported, by position
    covered = set()
    short = []
    for c, g in sorted(((len(v), k) for k, v in positions.items() if len(v) >= 5),
                       reverse=True):
        if all(t in FUNCTION_WORDS for t in g):
            continue
        inside = sum(1 for i in positions[g]
                     if any(j in covered for j in range(i, i + N4)))
        if inside > len(positions[g]) / 2:
            continue
        for i in positions[g]:
            covered.update(range(i, i + N4))
        short.append((c, g))
        if len(short) == 10:
            break

    if short:
        L.append("\n  Recurring short phrases (below the 10-word window):")
        L.append(f"  {'count':>7} {'per 10k words':>14}   phrase")
        L.append("  " + "-" * 68)
        for c, g in short:
            L.append(f"  {c:>7} {c / total_w * 10000:>14.1f}   "
                     f"{surface(raw, positions[g][0], N4)}")
        L.append("")
        L.append("  A book can score low on the headline number and still repeat a")
        L.append("  four-word habit every few hundred words. Compare the rate column")
        L.append("  against your own baseline, not against zero.")

    if count_phrases:
        flat = re.sub(r'\s+', ' ', normalize(raw)).lower()
        L.append("\n  Phrase counts you asked for:")
        L.append(f"  {'count':>7} {'per 10k words':>14}   phrase")
        L.append("  " + "-" * 68)
        for phrase in count_phrases:
            key = re.sub(r'\s+', ' ', normalize(phrase)).lower().strip()
            c = flat.count(key)
            L.append(f"  {c:>7} {c / total_w * 10000:>14.1f}   {phrase}")
        L.append("")
        L.append("  Use this to check a claim before you believe it. A beta reader,")
        L.append("  human or otherwise, can be confidently wrong about frequency.")

    if len(chs) > 2:
        first_ng = set(ngrams(words(chs[0][1])))
        L.append("\n  Drift check:")
        L.append(f"  {'ch':>4} {'words':>8} {'vs ch1':>8} {'self-rep':>9}")
        L.append("  " + "-" * 33)
        for i, (title, body) in enumerate(chs, start=1):
            ng = ngrams(words(body))
            if not ng:
                continue
            ov = len(set(ng) & first_ng) / len(set(ng))
            cc = Counter(ng)
            self_rep = sum(c for g, c in cc.items() if c > 1) / len(ng)
            L.append(f"  {i:>4} {len(body.split()):>8,} {pct(ov):>8} {pct(self_rep):>9}")
        L.append("")
        L.append("  Overlap with chapter 1 that climbs toward the end means the")
        L.append("  model started recycling its own earlier material.")

    L.append("\n" + "=" * 72)
    emit(L, out)


def main():
    p = argparse.ArgumentParser(
        description="Measure AI carryover and internal repetition in a manuscript.")
    sub = p.add_subparsers(dest='mode', required=True)

    c = sub.add_parser('compare', help='edited manuscript vs the AI draft')
    c.add_argument('ai_draft')
    c.add_argument('edited')

    r = sub.add_parser('repetition', help='how much one book repeats itself')
    r.add_argument('book')
    r.add_argument('--chapters-only', action='store_true',
                   help='strip front and back matter; measure the narrative only')
    r.add_argument('--count', action='append', metavar='PHRASE',
                   help='count an exact phrase (repeatable) - use it to check '
                        'a claim that something is overused')

    for s in (c, r):
        s.add_argument('--chapter-regex', default=None,
                       help='custom regex for chapter headings')
        s.add_argument('--out', default=None, help='write report to a file')

    a = p.parse_args()
    if a.mode == 'compare':
        compare(a.ai_draft, a.edited, a.chapter_regex, a.out)
    else:
        repetition(a.book, a.chapter_regex, a.out, a.chapters_only, a.count)


if __name__ == '__main__':
    main()
