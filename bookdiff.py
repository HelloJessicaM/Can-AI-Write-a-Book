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

  Loosen or tighten the motif floor (default 1.5 per 10,000 words):

      python bookdiff.py repetition my_book.txt --motif-rate 0.8

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

MOTIFS     - the tier below SHORT TICS: a single image that recurs all
             book long. "golden thread" 110 times in 72,000 words shares
             no 10-word window with itself and is not a four-word habit,
             so neither number above sees it -- but a reader meets it
             every other page and calls the book repetitive. The
             'chapters' column is the important one: 29/30 is a style
             tic, 3/30 at the same count is a runaway passage.

FRAME      - the narrator referring to its own construction: "as she had
             done in Chapter 18", "the chapter ended", a stray word
             count, assistant chatter that survived into the prose.
             These are generation defects, not style. No quality score
             catches them and they are free to find.

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
    # One tolerant pattern first, because a single export mixes heading shapes:
    # '## Chapter 5: Title', '# Chapter 5', 'Chapter 5', '**Chapter 5 - Title**'
    # (normalize() has already stripped the asterisks by the time we match).
    # The old list took the FIRST pattern matching 3+ lines, so a file using two
    # shapes was split on whichever shape appeared first and the rest of the
    # chapters were silently swallowed into their predecessors.
    r'^#{0,6}\s*Chapter\s+(?:\d+|[IVXLC]+)\b',
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


# Common English, plus the narrative verbs and body-part nouns that appear at
# motif-like rates in any novel. A one-word motif has to clear this list to be
# worth reporting; "mirror" does, "looked" does not.
MOTIF_STOP = set("""a about above after again against all almost alone along already also
although always am among an and another any anything are around as ask asked at away back
be became because become been before began begin behind being below beside best better
between beyond both but by call called came can cannot can't come comes coming could
couldn't did didn't do does doesn't doing don't done down during each either else enough
even ever every everything except far feel feeling feels felt few finally first for found
from front get gets getting give given giving go goes going gone good got great had hadn't
half has hasn't have haven't having he head heard hear her here hers herself him himself
his hold how however i if in inside instead into is isn't it its it's itself just keep
kept knew know known last late later least leave leaving led left less let like little
long look looked looking looks made make makes making many may maybe me mean might mind
mine moment more moment's morning most move moved much must my myself near need needed
never new next no none nor not nothing now of off often on once one only onto or other
others our out over own part people perhaps place put quite rather reached really right
room said same saw say saying says second see seemed seems seen set several shall she
should shouldn't side since slowly small so some something sometimes soon stand standing
started still stood stop such sure take taken taking tell than that that's the their them
then there there's these they they'd they're thing things think thinking this those though
thought three through time times to together told too took toward towards tried try trying
turn turned two under until up upon us use used very voice wait walked want wanted was
wasn't watched way we well went were weren't what whatever when where whether which while
who whole why will with within without woman words would wouldn't yes yet you young your
yours yourself
across beneath underneath throughout amid amidst atop despite unless whereas somewhere
anywhere everywhere nowhere barely hardly simply merely truly surely clearly suddenly
certainly probably possibly actually exactly entirely completely somehow therefore thus
hence meanwhile afterward beforehand elsewhere likewise neither nobody anybody everybody
someone anyone everyone having doing being gotten able""".split())


# Frequent everyday content words. A one-word motif has to be rarer than these
# to mean anything: "mirror" at 14 per 10,000 words is a motif because mirrors
# are rare in English, while "hand" at 40 is just a novel. Ranking on raw count
# alone puts "chair" above "mirror", which is the wrong answer. Phrases are not
# filtered -- a repeated two-word collocation is motif-like whatever it is made
# of. --motif-all turns this off.
COMMON_CONTENT = set("""time year people way day man thing woman life child world school
state family student group country problem hand part place case week company system
program question work government number night point home water room mother area money
story fact month lot right study book eye job word business issue side kind head house
service friend father power hour game line end member law car city community name
president team minute idea kid body information parent face level office door health
person art war history party result change morning reason research girl guy moment air
teacher force education foot boy age policy process music market sense nation plan
college interest death experience effect use class control care field development role
effort rate heart drug show leader light voice wife police mind price report decision
son view relationship town road arm difference value building action model season
society tax director position player record paper space ground form support event
official whole order matter type security course industry wall term attention table
chair window floor glass phone note week surface weight shape edge colour color shadow
distance sound silence memory feeling thought answer street rain coffee desk screen
say get make go know take see come think look want give use find tell ask work seem
feel try leave call need become mean keep begin help talk turn start hear play run
move like live believe hold bring happen write provide sit stand lose pay meet
include continue set learn lead understand watch follow stop create speak read allow
add spend grow open walk win offer remember love consider appear buy wait serve die
send expect build stay fall cut reach remain good new first last long great little
own other old big high different small large next early young important public bad
same able sure real whole full close""".split())


def motif_key(tok):
    """Light plural merge so 'thread' and 'threads' count as the same motif.

    Deliberately crude. It must not touch the numbers in the sections above,
    so it is used only when counting motifs.
    """
    if len(tok) >= 4 and tok.endswith('s') and not tok.endswith(('ss', 'us', 'is', "'s")):
        return tok[:-1]
    return tok


# Membership is tested against plural-merged keys, so merge the list too.
COMMON_SINGLES = {motif_key(w) for w in COMMON_CONTENT} | COMMON_CONTENT


def proper_nouns(text):
    """Tokens usually capitalised mid-sentence: names, places, invented things.

    Reported separately from common-noun motifs. A protagonist's name at 300
    per 10,000 words is normal and says nothing; 'Seattle skyline' at 1.8 is
    a motif, and so is whatever the model decided to call its own invention.
    """
    flat = re.sub(r'\s+', ' ', normalize(text))
    cap, low, ever_low, total = Counter(), Counter(), set(), Counter()
    for m in re.finditer(r"[A-Za-z’']+", flat):
        w = motif_key(m.group().lower())
        total[w] += 1
        if not m.group()[0].isupper():
            ever_low.add(w)
        before = flat[:m.start()].rstrip()
        if not before or before[-1] in '.!?"”:;—':
            continue                      # sentence-initial tells us nothing
        (cap if m.group()[0].isupper() else low)[w] += 1
    mid = {w for w, c in cap.items() if c >= 3 and c > 2 * low.get(w, 0)}
    # A name that almost always opens its sentence has no mid-sentence evidence
    # at all. If it is never once seen lower-case, it is still a name.
    always = {w for w, c in total.items() if c >= 8 and w not in ever_low}
    return mid | always


def body_stream(chs):
    """Chapter bodies as one token stream, with the chapter each token sits in.

    Joining with a newline keeps tokenisation of the joined text aligned
    index-for-index with both key lists, so surface() still works for display.
    Returns (joined_text, raw_keys, merged_keys, chapter_of_token).
    """
    raw, merged, chap, parts = [], [], [], []
    for ci, (_, body) in enumerate(chs):
        ks = [k for k, _, _ in tokens(body)]
        raw.extend(ks)
        merged.extend(motif_key(k) for k in ks)
        chap.extend([ci] * len(ks))
        parts.append(body)
    return '\n'.join(parts), raw, merged, chap


def repeat_mask(keys, n=WINDOW):
    """Token positions sitting inside a 10-word window that occurs more than once.

    Everything above already reports these. Masking them is what makes the
    motif list show the repetition the headline number CANNOT see, instead of
    restating a runaway passage in four-word pieces. On a clean book this
    removes almost nothing; on a looping one it removes the loop and leaves
    the model's actual style tics visible.
    """
    counts = Counter(tuple(keys[i:i + n]) for i in range(len(keys) - n + 1))
    bad = set()
    for i in range(len(keys) - n + 1):
        if counts[tuple(keys[i:i + n])] > 1:
            bad.update(range(i, i + n))
    return bad


def find_motifs(joined, rawkeys, keys, chap, rate_floor=1.5, limit=14, show_all=False):
    """Phrases of 1-4 words recurring book-wide, longest form first.

    Longest-first with position coverage means 'the mirror in the machine'
    is reported as itself and 'mirror' is still reported for the other
    ninety-odd times it appears alone. A shorter phrase is dropped only
    when most of its surviving occurrences sit inside something already listed.
    """
    total = len(keys)
    if total < 500:
        return [], [], [], total, 0, 0
    masked = repeat_mask(rawkeys)
    # Rate and floor are measured against the whole body, never against what
    # survived the mask. The per-10k column exists so two books of different
    # lengths can be compared; a denominator that shrinks with how loopy the
    # book is would make exactly that comparison meaningless.
    floor = max(5, int(round(rate_floor * total / 10000)))
    propers = proper_nouns(joined)

    grams = {}
    for n in (1, 2, 3, 4):
        d = {}
        for i in range(total - n + 1):
            if any(j in masked for j in range(i, i + n)):
                continue
            d.setdefault(tuple(keys[i:i + n]), []).append(i)
        grams[n] = d

    def expand(g, n, pos):
        """The most specific phrase that still explains most of these hits.

        A motif is short by nature, so the scan runs shortest-first. Without
        this step 'golden thread' reports as a bare 'thread', and a four-word
        accident of context ('thread pulled at her') outranks the image it
        contains. Returns the longest phrase around g covering 60%+ of its hits.
        """
        need = 0.6 * len(pos)
        for n2 in (4, 3, 2):
            if n2 <= n:
                break
            best = None
            for o in range(n2 - n + 1):
                c = Counter()
                for i in pos:
                    s = i - o
                    if s < 0 or s + n2 > total:
                        continue
                    if any(j in masked for j in range(s, s + n2)):
                        continue
                    c[tuple(keys[s:s + n2])] += 1
                if not c:
                    continue
                g2, hits = c.most_common(1)[0]
                if hits >= need and g2 in grams[n2] and (not best or hits > best[1]):
                    best = (g2, hits)
            if best:
                return best[0], n2
        return g, n

    covered, common, named = set(), [], []
    for n in (1, 2, 3, 4):
        for c, g in sorted(((len(v), g) for g, v in grams[n].items() if len(v) >= floor),
                           reverse=True):
            if n == 1:
                if g[0] in MOTIF_STOP or len(g[0]) < 3:
                    continue
                if not show_all and g[0] in COMMON_SINGLES:
                    continue
            elif all(t in FUNCTION_WORDS or t in MOTIF_STOP for t in g):
                continue
            elif not show_all and c < floor * 4 and all(
                    t in FUNCTION_WORDS or t in MOTIF_STOP or t in COMMON_SINGLES
                    for t in g):
                # Made entirely of everyday words, so it needs to be far more
                # frequent before it means anything. This keeps "for the first
                # time" (every one of these books says it 50+ times) and drops
                # "the phone" and "table were".
                continue
            pos = grams[n][g]
            if sum(1 for i in pos
                   if any(j in covered for j in range(i, i + n))) > len(pos) / 2:
                continue
            g2, n2 = expand(g, n, pos)
            pos2 = grams[n2].get(g2) or pos
            # Test coverage AGAIN on the expanded span. 'golden' and 'thread'
            # are both live single words that expand to overlapping four-word
            # phrases; without this the same motif lists twice at two offsets.
            if sum(1 for i in pos2
                   if any(j in covered for j in range(i, i + n2))) > len(pos2) / 2:
                continue
            for i in pos:
                covered.update(range(i, i + n))
            for i in pos2:
                covered.update(range(i, i + n2))
            row = (len(pos2), len(pos2) / total * 10000,
                   len({chap[i] for i in pos2}), surface(joined, pos2[0], n2))
            (named if any(t in propers for t in g2) else common).append(row)

    # A motif with a high count inside two or three chapters is a runaway
    # passage, not a style tic -- the single most useful row in the section.
    # Ranked by count it loses its seat to ordinary vocabulary, so it gets a
    # guaranteed one.
    n_ch = max(chap) + 1 if chap else 1
    narrow = max(3, n_ch // 5)
    conc = [r for r in common if r[2] <= narrow]
    spread_out = [r for r in common if r[2] > narrow]
    for seq in (spread_out, conc, named):
        seq.sort(key=lambda r: -r[0])
    return spread_out[:limit], conc[:6], named[:6], total, len(masked), narrow


CH_NUM = (r'(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|'
          r'twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
          r'twent(?:y|ieth)|thirt(?:y|ieth)|[ivxlc]{1,5})')

# "the next chapter of her life" is ordinary English, not the narrator
# describing the document. In prose about personal transformation it turns up
# constantly, and it was 2 of the first 23 hits in the field test.
CHAPTER_IDIOM = (r'(?!\s+(?:of|in)\s+(?:her|his|their|its|my|your|our)\s+'
                 r'(?:life|lives|story|journey|existence|career|past|marriage|'
                 r'journal|journals|diary|notebook|memoir|book))')

# Deliberately narrow. A check that cries wolf gets ignored, and every hit
# here should be something you would actually cut.
FRAME_CHECKS = [
    ('chapter ref', re.compile(r'\bchapters?\s+' + CH_NUM + r'\b', re.I)),
    ('meta narration', re.compile(
        r'\b(?:this|the|that|previous|preceding|following|final|opening|'
        r'closing|earlier|later)\s+chapters?\b' + CHAPTER_IDIOM + r'|'
        r'\bchapter\s+(?:ended|began|opened|closed|closes|opens|concludes)\b', re.I)),
    ('production note', re.compile(
        r'\bword count\b|\bchapter summary\b|\bto be continued\b|'
        r'\bapproximate(?:ly)?\s+[\d,]+\s+words\b|\[note|\(note to', re.I)),
    ('model voice', re.compile(
        r'\bas an ai\b|\bas a language model\b|'
        r'\bi hope (?:this|that) (?:helps|is)\b|\bi apologi[sz]e for\b|'
        r'^(?:certainly|sure)[!,]|\bhere(?:\'s| is) (?:the|your) (?:chapter|next)\b|'
        # The assistant answering the prompt instead of writing the book. Both
        # of these turned up in real gemma4:12b runs: a chapter that opened by
        # discussing its own missing input, and one that announced its task.
        r'\bno (?:preceding|previous) text\b|\bthe text (?:provided|above|below)\b|'
        r'^i will (?:begin|now|continue|start|write)\b|\bbegin a new narrative\b|'
        r'\bbased on the (?:prompt|outline|instruction|text provided)\b|'
        r'\bas (?:requested|instructed)\b|\bcontinu(?:e|ing) the (?:story|narrative)\b', re.I)),
    ('addresses reader', re.compile(
        r'\bdear reader\b|\bthe reader (?:will|should|may|might|can)\b|'
        r'\bas (?:we|you) (?:saw|have seen|will see)\b|'
        r'\bas (?:mentioned|noted|discussed) (?:earlier|above|previously)\b', re.I)),
]


# A line that is itself a heading, whatever shape it takes and whether or not
# the splitter happened to recognise it. Belt and braces: without this, one
# unrecognised heading shape turns thirty headings into thirty false positives.
HEADING_LIKE = re.compile(
    r'^#{0,6}\s*(?:chapter|part|book|section|prologue|epilogue|interlude)\b'
    r'[^.!?]{0,80}$', re.I)


def frame_breaks(raw, custom=None, limit=40):
    """Lines in the narrative where the prose refers to its own construction.

    Chapter headings are skipped (they are supposed to say 'Chapter 12'), and
    so is everything from the first back-matter heading on, because endnotes
    reference chapter numbers legitimately. Line numbers are real file lines.
    """
    lines = normalize(raw).split('\n')
    marks, _ = chapter_marks(lines, custom)
    mark_set = set(marks)
    start = marks[0] if marks else 0
    end = next((i for i in range(start, len(lines))
                if BACK_MATTER.match(lines[i].strip())), len(lines))

    hits = []
    for i in range(start, end):
        if i in mark_set:
            continue
        s = lines[i].strip()
        if not s or HEADING_LIKE.match(s):
            continue
        for kind, rx in FRAME_CHECKS:
            m = rx.search(s)
            if not m:
                continue
            a, b = max(0, m.start() - 48), min(len(s), m.end() + 48)
            snip = ('...' if a else '') + s[a:b] + ('...' if b < len(s) else '')
            hits.append((i + 1, kind, ' '.join(snip.split())))
            break
    return hits[:limit], len(hits)


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


def repetition(path, chapter_regex, out, chapters_only=False, count_phrases=None,
               motif_rate=1.5, motif_all=False):
    raw = read(path)
    orig_raw = raw          # frame-break line numbers must match the real file
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

    # motif saturation: one image, all book long. Invisible to everything above.
    joined, mraw, mkeys, mchap = body_stream(chs)
    mot, conc, named, mlive, mmasked, narrow = find_motifs(
        joined, mraw, mkeys, mchap, motif_rate, show_all=motif_all)
    if mot or conc or named:
        row = lambda c, r, s, t, tag='': (
            f"  {c:>7} {r:>8.1f} {f'{s}/{len(chs)}':>9}   {t}{tag}")
        L.append("\n  Motif saturation (recurring images, measured over "
                 f"{len(chs)} chapters):")
        L.append(f"  {'count':>7} {'per 10k':>8} {'chapters':>9}   phrase")
        L.append("  " + "-" * 68)
        for c, rate, spread, text in mot:
            L.append(row(c, rate, spread, text))
        if named:
            L.append("  " + "." * 68)
            for c, rate, spread, text in named:
                L.append(row(c, rate, spread, text, '   (name/place)'))
        if conc:
            L.append("  " + "." * 68)
            for c, rate, spread, text in conc:
                L.append(row(c, rate, spread, text, '   <- CONCENTRATED'))
        L.append("")
        L.append("  Read the 'chapters' column first. A phrase in nearly every chapter")
        L.append("  is a style tic: the model's voice, and it will be in every book it")
        L.append("  writes. The same count inside two or three chapters is a runaway")
        L.append("  passage, which is a different problem with a different fix.")
        L.append("  Singular and plural are merged here and nowhere else in this report.")
        L.append("  Names and places are separated out because they recur for ordinary")
        L.append(f"  reasons. CONCENTRATED rows sit in {narrow} chapters or fewer and are")
        L.append("  listed whatever their count, because ranking on count alone loses")
        L.append("  them behind ordinary vocabulary -- and they are the ones to read.")
        L.append(f"  Floor: {motif_rate} per 10,000 words "
                 f"(>= {max(5, int(round(motif_rate * mlive / 10000)))} occurrences "
                 f"in {mlive:,} words). --motif-rate changes it.")
        if mmasked:
            L.append(f"  {mmasked:,} of those words ({mmasked / mlive * 100:.0f}%) sit inside a "
                     f"repeated 10-word")
            L.append("  window and were skipped, so what is left is the repetition the")
            L.append("  headline number cannot see rather than a restatement of it.")

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

    # frame breaks: generation defects. Not style, not repetition, just wrong.
    hits, n_hits = frame_breaks(orig_raw, chapter_regex)
    L.append(f"\n  Frame breaks: {n_hits}")
    if hits:
        L.append(f"  {'line':>6}  {'kind':<16}  text")
        L.append("  " + "-" * 68)
        for ln_no, kind, text in hits:
            L.append(f"  {ln_no:>6}  {kind:<16}  {text}")
        if n_hits > len(hits):
            L.append(f"  ... and {n_hits - len(hits)} more.")
        L.append("")
        L.append("  Each of these is the narrator describing the document instead of")
        L.append("  the story. Chapter headings and back matter are excluded, so a hit")
        L.append("  is prose. Read them: a character can mention a chapter of a book")
        L.append("  for real reasons, and this check cannot tell the difference.")
    else:
        L.append("  No prose referring to chapter numbers, word counts, or itself.")

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
    r.add_argument('--motif-rate', type=float, default=1.5, metavar='PER10K',
                   help='motif floor, occurrences per 10,000 words (default 1.5, '
                        'about once every 6,700 words). Lower it to see more.')
    r.add_argument('--motif-all', action='store_true',
                   help='include frequent everyday words (hand, light, room) as '
                        'single-word motifs. Off by default: they outrank real '
                        'motifs on count and tell you nothing.')

    for s in (c, r):
        s.add_argument('--chapter-regex', default=None,
                       help='custom regex for chapter headings')
        s.add_argument('--out', default=None, help='write report to a file')

    a = p.parse_args()
    if a.mode == 'compare':
        compare(a.ai_draft, a.edited, a.chapter_regex, a.out)
    else:
        repetition(a.book, a.chapter_regex, a.out, a.chapters_only, a.count,
                   a.motif_rate, a.motif_all)


if __name__ == '__main__':
    main()
