#!/usr/bin/env python3
"""
runqc.py - Run every check on a finished book, in order, and print the
           spreadsheet row when it's done.

One command per run. Never wonder again which steps you already did.

Python 3.8+. Needs bookdiff.py and makebody.py in the same folder.
chaptertimes.ps1 too, if you want it run for you.

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------

    python runqc.py G1 "C:\\books\\Affirmation_Glitch_gemma4"

        runs chaptertimes, builds the body file, runs bookdiff,
        audits for cloud-fallback chapters, writes a checklist,
        and prints the matrix row

    python runqc.py G1 "C:\\books\\..." --timings G1_timings.csv

        skip chaptertimes and use a CSV you already have

    python runqc.py G1 "C:\\books\\..." --out qc\\G1

        put everything in one folder per run

--------------------------------------------------------------------------
WHAT IT DOES, IN ORDER
--------------------------------------------------------------------------

1. chaptertimes  -> <run>_timings.csv
2. Fallback audit: flags any chapter generated far faster than the run's
   median. A cloud fallback chapter can run 3x to 130x the local rate.
   This is a backstop, not proof. It cannot catch a fallback whose
   recorded time includes the retries that preceded it - one known
   Gemini chapter came out SLOWER than its run's median for exactly that
   reason. The engine label BookyAI shows per chapter is the only
   reliable check, and it belongs on the checklist every time.
3. Assembles <run>_body.md from chapter_01.md ... chapter_NN.md, in order.
   This is what the judges read and what bookdiff measures. Building it
   from the chapter files means no hunting through BookyAI's timestamped
   export folders, and no risk of measuring outline.md by mistake.
   Pass --export to measure a real export instead.
4. bookdiff      -> <run>_repetition.txt (--chapters-only)
5. <run>_checklist.md with the manual steps that have to happen in the
   app or the browser, in the order they have to happen
6. Prints the experiment-matrix row, tab separated, ready to paste

RUN THIS THE DAY THE BOOK FINISHES. BookyAI's own quality check has to be
run inside the app before you touch anything, and it takes over an hour
for 30 chapters, so start it first and let it work while you do the rest.

--------------------------------------------------------------------------
MIT licensed.
--------------------------------------------------------------------------
"""

import argparse
import csv
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime

TARGET_WORDS = 3000
HERE = os.path.dirname(os.path.abspath(__file__))

MATRIX_COLS = [
    'run_id', 'machine', 'model_tag', 'thinking', 'num_ctx', 'reported_size_gb',
    'cpu_gpu_split', 'ch 1. finish time', 'last_chapter_finish_time',
    'wall_clock_min', 'status', 'chapters_completed', 'chapters_truncated',
    'total_words', 'total_tokens', 'tok_per_sec', 'median_chapter_sec',
    'slowest_chapter_sec', 'adherence_pct_per_chapter', 'repetition_pct',
    'drift_ch30_vs_ch1', 'kwh', 'electricity_usd_at_0.15_illustrative',
    'bookyai_version', 'notes', 'old_run_id']


def run(cmd, label):
    print(f"  -> {label}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print(f"     skipped: {cmd[0]} not found")
        return None
    if r.returncode != 0:
        print(f"     failed: {(r.stderr or r.stdout).strip()[:300]}")
        return None
    return r.stdout


def parse_time(s):
    for f in ('%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M'):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp: {s}")


def read_timings(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    ch = [r for r in rows if r['File'].lower().startswith('chapter_')]
    if not ch:
        sys.exit(f"No chapter_*.md rows in {path}.")
    return ch


def metrics(ch):
    d = [parse_time(r['Finished']) for r in ch]
    w = [int(r['Words']) for r in ch]
    t = [int(r['Tokens']) for r in ch]
    timed = [(int(r['Tokens']), float(r['Seconds'])) for r in ch[1:] if r['Seconds']]
    # Chapter 1's Seconds is the gap since whatever came before it -- usually
    # the project folder being created, sometimes an overnight idle. It is not
    # a generation time. tok/s already excluded it; median and slowest did not,
    # which reported "slowest chapter 3,409 s" inside a 49-minute run.
    secs = [float(r['Seconds']) for r in ch[1:] if r['Seconds']]
    return {
        'ch1': d[0], 'chN': d[-1],
        'wall': round((d[-1] - d[0]).total_seconds() / 60, 1),
        'chapters': len(ch),
        'truncated': sum(1 for r in ch if r['Truncated'] == 'YES'),
        'words': sum(w), 'tokens': sum(t),
        # chapter 1 leaves both sides of this ratio: its duration is unmeasurable
        'tps': round(sum(a for a, _ in timed) / sum(b for _, b in timed), 2),
        'median_sec': round(sorted(secs)[len(secs) // 2], 1),
        'slowest_sec': round(max(secs), 1),
        'adherence': round(sum(w) / (len(ch) * TARGET_WORDS) * 100),
        'median_words': int(statistics.median(w)),
        'longest': max(w), 'shortest': min(w),
        'in_band': sum(1 for x in w if 0.8 * TARGET_WORDS <= x <= 1.2 * TARGET_WORDS),
    }


def fallback_audit(ch):
    """A chapter generated far faster than the rest was not written locally."""
    rates = [(r['File'], float(r['TokPerSec'])) for r in ch if r.get('TokPerSec')]
    if len(rates) < 5:
        return [], 0
    med = statistics.median(x[1] for x in rates)
    return [(f, v, round(v / med, 1)) for f, v in rates if v > 2.5 * med], med


# any shape of chapter heading the models and BookyAI actually emit
# The trailing delimiter used to be required, so 'L1-16kr2-4070' read as
# UNKNOWN and had to be filled by hand. A leading delimiter plus "not followed
# by a digit" is enough to pin it, and keeps '128' with no k ambiguous (blank).
CTX_IN_ID = re.compile(r'(?:^|[-_])(\d{1,3})\s*k(?![0-9])', re.I)


def ctx_from_run_id(run_id):
    """8192 from 'L1-8k-r2', 16384 from 'G1-16k'. None if the id says nothing.

    Never fall back to a default. A wrong num_ctx silently invalidates the
    context experiment, and a blank cell is honest where 32768 is a lie.
    """
    m = CTX_IN_ID.search(run_id)
    return int(m.group(1)) * 1024 if m else None


CH_HEADING = re.compile(r'^\s*[#*_\s]*chapter\s+(\d+|[ivxlc]+)\s*[:.\-\u2013\u2014]?\s*(.*?)[#*_\s]*$', re.I)


def assemble(folder, ch_rows, out_path):
    """Build the body from the chapter files, in the order chaptertimes found them.

    BookyAI exports into timestamped subfolders and drops an outline.md beside
    the chapters, so guessing at 'the export' is how you end up measuring a
    1,151-word outline and reporting 0.0% repetition.
    """
    parts = []
    for i, r in enumerate(ch_rows, start=1):
        path = os.path.join(folder, r['File'])
        if not os.path.exists(path):
            print(f"     missing {r['File']}")
            continue
        text = None
        for enc in ('utf-8', 'utf-8-sig', 'cp1252', 'latin-1'):
            try:
                text = open(path, encoding=enc).read()
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            print(f"     could not read {r['File']}")
            continue
        lines = text.split('\n')
        j = next((k for k, ln in enumerate(lines) if ln.strip()), None)
        title = ''
        if j is not None:
            hit = CH_HEADING.match(lines[j].strip())
            if hit:
                # replace the file's own heading with a uniform one, whatever
                # shape it came in: "Chapter 1", "# Chapter 1: Title",
                # "**Chapter One: Title**". Mixed shapes are the problem -
                # bookdiff accepts the first pattern that matches 3+ lines, so
                # one odd heading in chapter 1 silently becomes front matter.
                title = hit.group(2).strip()
                lines = lines[j + 1:]
        head = f"## Chapter {i}" + (f": {title}" if title else "")
        text = head + "\n\n" + '\n'.join(lines).strip()
        parts.append(text.rstrip())
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(parts) + '\n')
    return len(parts)


def checklist(run_id, m, flagged, out_dir, source_folder=''):
    L = [f"# QC checklist - {run_id}", "",
         f"Book finished {m['chN']:%Y-%m-%d %H:%M}. Checklist generated {datetime.now():%Y-%m-%d %H:%M}.",
         "",
         f"Source folder: `{source_folder}`",
         "",
         "**Check that folder name against the run id above before you paste anything.**",
         "", "## Do these in the app first - they take the longest", "",
         "- [ ] **BookyAI quality check** (Quality review -> originality + proofread).",
         "      About 75 minutes for 30 chapters, so start it before anything else.",
         "      Export the JSON and save it here as `" + run_id + "_bookyai_quality.json`.",
         "- [ ] Record `ollama ps` SIZE and CPU/GPU split. Reload the model if the run has",
         "      already ended - the split is the same for the same model, context and machine.",
         "- [ ] Confirm the engine label on every chapter is your local model.",
         "", "## Already done by this script", "",
         f"- [x] chaptertimes -> `{run_id}_timings.csv`",
         f"- [x] body extract  -> `{run_id}_body.md`  (what every judge reads)",
         f"- [x] bookdiff repetition --chapters-only -> `{run_id}_repetition.txt`",
         "- [x] fallback audit (see below)", ""]

    L += ["## Fallback audit", ""]
    if flagged:
        L.append("**Chapters generated far faster than this run's median. Check the engine")
        L.append("label on each one before trusting this book:**")
        L.append("")
        for f, v, x in flagged:
            L.append(f"- [ ] `{f}` - {v} tok/s, {x}x the median")
        L.append("")
        L.append("If any was written by the fallback engine, regenerate it locally and re-run this script.")
    else:
        L.append("No chapter stands out from this run's own rate.")
        L.append("")
        L.append("**This does not prove the run was all local.** A fallback whose recorded time")
        L.append("includes the retries before it can come out slower than the median - that is")
        L.append("exactly what happened to one known Gemini chapter. Check the engine labels.")
    L += ["", "## Then, in this order", "",
          "- [ ] AutoCrit Score, **General Fiction** (locked for the whole series)",
          f"- [ ] ChatGPT manuscript judge on `{run_id}_body.md`, three fresh chats, record all three and the median",
          "- [ ] Paste the matrix row below into `experiment matrix`",
          "- [ ] Paste the scores into `quality_scores` (tool_type: deterministic or judge)",
          "", "### Finalists only", "",
          "- [ ] Cozy / Uplift reader in AutoCrit",
          "- [ ] Gemini via Google Workspace (`.md` only - it reads less of a `.docx`)",
          "- [ ] AutoCrit Analyzer+ - read the **timeline** section for chapters that repeat earlier chapters",
          "", "## Notes", "", "", ""]
    p = os.path.join(out_dir, f"{run_id}_checklist.md")
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    return p


def main():
    p = argparse.ArgumentParser(description="Run every check on a finished book, in order.")
    p.add_argument('run_id', help='the run id, e.g. G1')
    p.add_argument('book_folder', help='folder holding chapter_01.md ... and the export')
    p.add_argument('--timings', default=None, help='existing chaptertimes CSV; skips chaptertimes')
    p.add_argument('--export', default=None,
                   help='measure this exported .md instead of assembling from the chapter files')
    p.add_argument('--out', default='.', help='where to write outputs (default: here)')
    p.add_argument('--num-ctx', type=int, default=None,
                   help='context window this run used. Omitted, it is read from the run id '
                        '("-8k" -> 8192, "-16k" -> 16384); if the id says nothing the cell is '
                        'left BLANK rather than guessed.')
    p.add_argument('--thinking', default='OFF', help="value for the thinking column (default OFF)")
    p.add_argument('--machine', default='', help='machine name, e.g. boo-4070')
    p.add_argument('--model', default='', help='model tag, e.g. gemma4:26b')
    a = p.parse_args()

    os.makedirs(a.out, exist_ok=True)
    print(f"\n{a.run_id}\n" + "=" * 60)

    # 1. timings
    csv_path = a.timings or os.path.join(a.out, f"{a.run_id}_timings.csv")
    if not os.path.exists(csv_path):
        script = os.path.join(HERE, 'chaptertimes.ps1')
        run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', script,
             '-RunId', a.run_id, '-Path', a.book_folder, '-OutFile', csv_path],
            'chaptertimes')
    if not os.path.exists(csv_path):
        sys.exit(f"No timings at {csv_path}. Run chaptertimes yourself and pass --timings.")
    ch = read_timings(csv_path)
    m = metrics(ch)
    print(f"  {m['chapters']} chapters, {m['words']:,} words, {m['wall']} min "
          f"({m['wall']/60:.1f} h), {m['tps']} tok/s")

    # 2. fallback audit
    flagged, med = fallback_audit(ch)
    if flagged:
        print(f"  !! {len(flagged)} chapter(s) far above the median rate of {med:.2f} tok/s:")
        for f, v, x in flagged:
            print(f"       {f}  {v} tok/s  ({x}x median)")
    else:
        print("  fallback audit: clean")

    # 3. body + 4. repetition
    body = os.path.join(a.out, f"{a.run_id}_body.md")
    rep = os.path.join(a.out, f"{a.run_id}_repetition.txt")
    if a.export:
        source = a.export
        run([sys.executable, os.path.join(HERE, 'makebody.py'), source, '--out', body], 'makebody')
    else:
        source = body
        n = assemble(a.book_folder, ch, body)
        print(f"  -> assembled {body} from {n} chapter files")
    run([sys.executable, os.path.join(HERE, 'bookdiff.py'), 'repetition', source,
         '--chapters-only', '--out', rep], 'bookdiff repetition --chapters-only')

    rep_pct = ''
    if os.path.exists(rep):
        for line in open(rep, encoding='utf-8'):
            if 'Internal repetition:' in line:
                rep_pct = line.split(':')[1].strip().split('%')[0]
                print(f"  repetition: {rep_pct}%")
                break

    # 5. checklist
    print(f"  -> checklist: {checklist(a.run_id, m, flagged, a.out, os.path.abspath(a.book_folder))}")

    # 6. the row
    ctx = a.num_ctx if a.num_ctx else ctx_from_run_id(a.run_id)
    if a.num_ctx:
        print(f"  num_ctx {ctx} (from --num-ctx)")
    elif ctx:
        print(f"  num_ctx {ctx} (read from the run id)")
    else:
        print("  !! num_ctx UNKNOWN - left blank. Put '-8k'/'-16k' in the run id, "
              "or pass --num-ctx, so the column is never wrong.")
        ctx = ''
    row = {c: '' for c in MATRIX_COLS}
    row.update({
        'run_id': a.run_id, 'num_ctx': ctx, 'thinking': a.thinking,
        'machine': a.machine, 'model_tag': a.model,
        'ch 1. finish time': f"{m['ch1']:%m/%d/%Y %H:%M}",
        'last_chapter_finish_time': f"{m['chN']:%m/%d/%Y %H:%M}",
        'wall_clock_min': m['wall'], 'status': 'COMPLETE',
        'chapters_completed': m['chapters'], 'chapters_truncated': m['truncated'],
        'total_words': m['words'], 'total_tokens': m['tokens'], 'tok_per_sec': m['tps'],
        'median_chapter_sec': m['median_sec'], 'slowest_chapter_sec': m['slowest_sec'],
        'adherence_pct_per_chapter': m['adherence'], 'repetition_pct': rep_pct,
        'kwh': 'MEASURE', 'electricity_usd_at_0.15_illustrative': 'DERIVE FROM KWH',
        'notes': (f"{m['in_band']} of {m['chapters']} chapters within +/-20% of target; "
                  f"median {m['median_words']:,} w, longest {m['longest']:,}, shortest {m['shortest']:,}."
                  + (f" FALLBACK AUDIT FLAGGED: {', '.join(f for f, _, _ in flagged)} - verify engine label."
                     if flagged else "")),
    })
    # one-row CSV for this run, plus an append-only ledger of every run
    single = os.path.join(a.out, f"{a.run_id}_matrixrow.csv")
    with open(single, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(MATRIX_COLS); w.writerow([row[c] for c in MATRIX_COLS])

    ledger = os.path.join(a.out, 'matrix_rows.csv')
    seen = set()
    if os.path.exists(ledger):
        with open(ledger, newline='', encoding='utf-8-sig') as f:
            seen = {r['run_id'] for r in csv.DictReader(f) if r.get('run_id')}
    if a.run_id in seen:
        print(f"  -> {os.path.basename(ledger)} already has {a.run_id}; not appending again")
    else:
        new = not os.path.exists(ledger)
        with open(ledger, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new: w.writerow(MATRIX_COLS)
            w.writerow([row[c] for c in MATRIX_COLS])
        print(f"  -> appended to {os.path.basename(ledger)}")

    # per-chapter rows for the chapter_detail tab, straight out of the report
    if os.path.exists(rep):
        txt = open(rep, encoding='utf-8').read()
        if 'Drift check:' in txt:
            det = os.path.join(a.out, f"{a.run_id}_chapter_detail.csv")
            with open(det, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['run_id', 'chapter', 'words', 'vs_ch1_pct', 'self_rep_pct',
                            'report_is_post_chapters_only_fix'])
                n = 0
                for ln in txt.split('Drift check:')[1].split('\n'):
                    m = re.match(r'\s*(\d+)\s+([\d,]+)\s+([\d.]+)%\s+([\d.]+)%', ln)
                    if m and n < 60:
                        w.writerow([a.run_id, int(m.group(1)), int(m.group(2).replace(',', '')),
                                    float(m.group(3)), float(m.group(4)), 'yes'])
                        n += 1
            print(f"  -> {os.path.basename(det)} ({n} chapters) for the chapter_detail tab")

    print("\n" + "-" * 60)
    print("Paste into the experiment matrix (fill size, split, version):\n")
    print('\t'.join(str(row[c]) for c in MATRIX_COLS))
    print("-" * 60 + "\n")


if __name__ == '__main__':
    main()
