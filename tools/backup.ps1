<#
.SYNOPSIS
    Copies the PhoneHome archive to a folder you choose (an external drive, a second
    disk...), then checks every recording against SHA256SUMS.

.DESCRIPTION
    Additive only: this script never deletes anything from the backup.
      * New files are copied.
      * Recordings (.flac, .mp3) never change once made. If one differs between the
        archive and the backup, it is NOT overwritten: it's reported, and the check
        against SHA256SUMS tells you which copy is the good one.
      * Details and transcripts (.json, .txt...) can be edited in the web app. When one
        changed, the previous copy is moved to _previous\<date>\ first, then updated.
      * Messages deleted from the archive stay in the backup.

    Then every recording listed in SHA256SUMS is re-hashed in the backup, so you know
    the copy is good (use -SkipVerify to skip that on a slow drive).

    Works with Windows PowerShell 5.1 (built into Windows 10/11) and PowerShell 7.

.PARAMETER Source
    The archive: the NAS share, e.g. \\nas\phonehome (or a mapped drive like P:\).

.PARAMETER Destination
    Where the backup goes, e.g. E:\PhoneHome-Backup. Created if missing. Use the same
    folder every time: only new and changed files are copied.

.PARAMETER SkipVerify
    Don't re-hash the recordings after copying.

.PARAMETER Server
    Optional: the PhoneHome server, e.g. http://phonehome.lan:8080. With -Token, the
    result is reported there and shows on the Backup page ("last backup: 3 days ago").

.PARAMETER Token
    The server's PHONEHOME_BACKUP_TOKEN.

.EXAMPLE
    .\backup.ps1 -Source \\nas\phonehome -Destination E:\PhoneHome-Backup

.EXAMPLE
    .\backup.ps1 -Source \\nas\phonehome -Destination E:\PhoneHome-Backup -Server http://phonehome.lan:8080 -Token abc123
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Source,
    [Parameter(Mandatory = $true)] [string] $Destination,
    [switch] $SkipVerify,
    [string] $Server = '',
    [string] $Token = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$started = Get-Date
$stamp = $started.ToString('yyyy-MM-dd_HHmmss')
$problems = New-Object System.Collections.Generic.List[string]
$copied = 0
$updated = 0
$unchanged = 0
$bytesCopied = [int64]0

function Write-Log([string] $Message) {
    $line = '{0}  {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
}

function Add-Problem([string] $Message) {
    $script:problems.Add($Message)
    Write-Log "PROBLEM: $Message"
}

function Copy-Safely([string] $From, [string] $To) {
    # Copy to a temporary name first, so an interrupted copy never looks complete.
    $dir = Split-Path -Parent $To
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $partial = "$To.partial"
    Copy-Item -LiteralPath $From -Destination $partial -Force
    Move-Item -LiteralPath $partial -Destination $To -Force
}

# -- check the folders ----------------------------------------------------------------
$Source = $Source.TrimEnd('\', '/')
$Destination = $Destination.TrimEnd('\', '/')
if (-not (Test-Path -LiteralPath (Join-Path $Source 'SHA256SUMS'))) {
    if (-not (Test-Path -LiteralPath (Join-Path $Source 'messages'))) {
        throw "$Source doesn't look like a PhoneHome archive (no SHA256SUMS or messages folder). Is the NAS share connected?"
    }
}
if (-not (Test-Path -LiteralPath $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}
$script:LogFile = Join-Path $Destination 'backup-log.txt'
Write-Log "Backup started: $Source -> $Destination"

# -- copy --------------------------------------------------------------------------------
# SHA256SUMS first: it's a snapshot, so every file it lists already exists when we copy
# the rest (a message arriving mid-backup is simply picked up next time).
$sourceRoot = (Get-Item -LiteralPath $Source).FullName.TrimEnd('\', '/')
$files = New-Object System.Collections.Generic.List[object]
$sums = Get-Item -LiteralPath (Join-Path $Source 'SHA256SUMS') -ErrorAction SilentlyContinue
if ($sums) { $files.Add($sums) }
Get-ChildItem -LiteralPath $Source -Recurse -File -Force | ForEach-Object {
    $rel = $_.FullName.Substring($sourceRoot.Length).TrimStart('\', '/')
    $parts = $rel -split '[\\/]'
    $isHiddenFile = $_.Name.StartsWith('.')                 # locks and half-written temp files
    $inTempDir = $parts -contains '.tmp'
    if (-not $isHiddenFile -and -not $inTempDir -and $rel -ne 'SHA256SUMS') { $files.Add($_) }
}

$i = 0
foreach ($file in $files) {
    $i++
    $rel = $file.FullName.Substring($sourceRoot.Length).TrimStart('\', '/')
    if ($i % 25 -eq 0) {
        Write-Progress -Activity 'Copying the PhoneHome archive' -Status $rel -PercentComplete (100 * $i / $files.Count)
    }
    $target = Join-Path $Destination $rel
    try {
        if (-not (Test-Path -LiteralPath $target)) {
            Copy-Safely $file.FullName $target
            $copied++
            $bytesCopied += $file.Length
            continue
        }
        $existing = Get-Item -LiteralPath $target -Force
        # Some drives (FAT32/exFAT) store times to the nearest 2 seconds.
        $sameTime = [math]::Abs(($existing.LastWriteTimeUtc - $file.LastWriteTimeUtc).TotalSeconds) -le 2
        if ($existing.Length -eq $file.Length -and $sameTime) {
            $unchanged++
            continue
        }
        $isAudio = ($file.Extension -eq '.flac') -or ($file.Extension -eq '.mp3')
        $isRecording = $isAudio -and (($rel -like 'messages*') -or ($rel -like '.trash*'))
        if ($isRecording) {
            Add-Problem "$rel differs between the archive and the backup. Not overwritten; see the check below."
            continue
        }
        # An edited detail or transcript: keep the old copy, then update.
        $previous = Join-Path (Join-Path (Join-Path $Destination '_previous') $stamp) $rel
        $previousDir = Split-Path -Parent $previous
        if (-not (Test-Path -LiteralPath $previousDir)) { New-Item -ItemType Directory -Path $previousDir -Force | Out-Null }
        Move-Item -LiteralPath $target -Destination $previous -Force
        Copy-Safely $file.FullName $target
        $updated++
        $bytesCopied += $file.Length
    }
    catch {
        Add-Problem "Couldn't copy ${rel}: $($_.Exception.Message)"
    }
}
Write-Progress -Activity 'Copying the PhoneHome archive' -Completed
Write-Log ("Copied {0} new, updated {1}, {2} already up to date ({3:N1} MB copied)." -f `
    $copied, $updated, $unchanged, ($bytesCopied / 1MB))

# -- verify ------------------------------------------------------------------------------
$verified = 0
$sumsFile = Join-Path $Destination 'SHA256SUMS'
if ($SkipVerify) {
    Write-Log 'Skipped checking the recordings (-SkipVerify).'
}
elseif (-not (Test-Path -LiteralPath $sumsFile)) {
    Add-Problem 'No SHA256SUMS in the backup, so the recordings could not be checked.'
}
else {
    $lines = @(Get-Content -LiteralPath $sumsFile -Encoding UTF8 | Where-Object { $_ -match '^[0-9a-f]{64}  ' })
    $n = 0
    foreach ($line in $lines) {
        $n++
        $expected = $line.Substring(0, 64)
        $rel = $line.Substring(66)
        if ($n % 10 -eq 0) {
            Write-Progress -Activity 'Checking recordings against SHA256SUMS' -Status $rel -PercentComplete (100 * $n / $lines.Count)
        }
        $path = Join-Path $Destination ($rel -replace '/', '\')
        if (-not (Test-Path -LiteralPath $path)) {
            Add-Problem "$rel is listed in SHA256SUMS but missing from the backup."
            continue
        }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            # Which side is damaged? Check the archive's copy too.
            $srcPath = Join-Path $Source ($rel -replace '/', '\')
            $srcOk = $false
            if (Test-Path -LiteralPath $srcPath) {
                $srcOk = (Get-FileHash -LiteralPath $srcPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $expected
            }
            if ($srcOk) {
                Add-Problem "$rel is damaged in the BACKUP (the archive's copy is fine). Delete the backup's copy and run this again."
            }
            else {
                Add-Problem "$rel does not match SHA256SUMS in the backup or the archive. Check the Backup page in PhoneHome."
            }
            continue
        }
        $verified++
    }
    Write-Progress -Activity 'Checking recordings against SHA256SUMS' -Completed
    Write-Log "Checked $verified of $($lines.Count) recordings against SHA256SUMS."
}

# -- summary -----------------------------------------------------------------------------
$seconds = [int]((Get-Date) - $started).TotalSeconds
$summary = [ordered]@{
    at          = (Get-Date).ToString('o')
    destination = $Destination
    computer    = $env:COMPUTERNAME
    copied      = $copied
    updated     = $updated
    files       = $files.Count
    bytes       = $bytesCopied
    verified    = $verified
    problems    = @($problems | Select-Object -First 50)
    seconds     = $seconds
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Destination 'last-backup.json') -Encoding UTF8

if ($Server -and $Token) {
    try {
        $body = $summary | ConvertTo-Json -Depth 4
        Invoke-RestMethod -Method Post -Uri ($Server.TrimEnd('/') + '/api/backup/report') -ContentType 'application/json' `
            -Headers @{ Authorization = "Bearer $Token" } -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | Out-Null
        Write-Log "Reported to $Server."
    }
    catch {
        Write-Log "Couldn't report to ${Server}: $($_.Exception.Message) (the backup itself is fine)."
    }
}

if ($problems.Count -gt 0) {
    Write-Log "Finished in $seconds s with $($problems.Count) problem(s). See above and $script:LogFile."
    exit 1
}
Write-Log "Finished in $seconds s. Everything is backed up and checks out."
exit 0
