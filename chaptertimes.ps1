# chaptertimes.ps1
# Reads BookyAI's chapter .md files and works out how long each chapter took,
# using the gap between one chapter's last-write time and the next one's.
#
# RUN IT IN THE FOLDER WHERE BOOKYAI WROTE THE FILES, BEFORE MOVING THEM.
# Copying or moving files resets timestamps on Windows and destroys the data.
#
#   cd "C:\path\to\bookyai\output"
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force .\chaptertimes.ps1 -RunId A2 
#
# Writes <RunId>_timings.csv and prints a summary.

param(
    [string]$RunId = "run",
    [string]$Pattern = "*.md"
)

$files = Get-ChildItem -Filter $Pattern |
         Where-Object { $_.Name -notmatch 'outline|summary|character|appendix|reference' } |
         Sort-Object LastWriteTime

if ($files.Count -lt 2) {
    Write-Host "Found fewer than 2 chapter files. Check the folder and -Pattern." -ForegroundColor Red
    exit 1
}

$prev = $null
$rows = foreach ($f in $files) {
    $text  = Get-Content $f.FullName -Raw
    $words = ($text -split '\s+' | Where-Object { $_ }).Count

    # Ollama's own rule of thumb: ~1.33 tokens per English word
    $tokens = [math]::Round($words * 1.33)

    $secs = $null
    if ($prev) { $secs = [math]::Round(($f.LastWriteTime - $prev).TotalSeconds, 1) }

    $tps = $null
    if ($secs -and $secs -gt 0) { $tps = [math]::Round($tokens / $secs, 2) }

    # A chapter that stops without terminal punctuation is very likely truncated
    $tail = ($text.TrimEnd() -split '' | Select-Object -Last 1)
    $truncated = if ($text.TrimEnd() -match '[.!?"''\)\]]$') { "no" } else { "YES" }

    $prev = $f.LastWriteTime

    [pscustomobject]@{
        RunId     = $RunId
        File      = $f.Name
        Finished  = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
        Seconds   = $secs
        Words     = $words
        Tokens    = $tokens
        TokPerSec = $tps
        Truncated = $truncated
    }
}

$rows | Format-Table -AutoSize
$rows | Export-Csv "$($RunId)_timings.csv" -NoTypeInformation

$timed   = $rows | Where-Object { $_.Seconds -ne $null }
$totalW  = ($rows | Measure-Object Words -Sum).Sum
$totalT  = ($rows | Measure-Object Tokens -Sum).Sum
$span    = ([datetime]$rows[-1].Finished - [datetime]$rows[0].Finished).TotalMinutes
$median  = ($timed | Sort-Object Seconds)[[int]($timed.Count / 2)].Seconds
$slowest = ($timed | Sort-Object Seconds -Descending | Select-Object -First 1).Seconds
$cut     = ($rows | Where-Object { $_.Truncated -eq 'YES' }).Count

Write-Host ""
Write-Host "==================== $RunId ====================" -ForegroundColor Cyan
Write-Host ("  chapters              : {0}" -f $rows.Count)
Write-Host ("  wall clock (ch1->end) : {0:N1} min" -f $span)
Write-Host ("  total words           : {0:N0}" -f $totalW)
Write-Host ("  overall tok/sec       : {0:N2}" -f ($(if ($span) { $totalT / ($span * 60) } else { 0 })))
Write-Host ("  median chapter        : {0}s" -f $median)
Write-Host ("  slowest chapter       : {0}s" -f $slowest)
Write-Host ("  likely truncated      : {0}" -f $cut) -ForegroundColor $(if ($cut) { 'Yellow' } else { 'Green' })
Write-Host ("  target adherence      : {0:N0}% of 90,000" -f ($totalW / 90000 * 100))
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Note: the gap before chapter 1 is not measured, since that interval"
Write-Host "includes your manual outline approval."
