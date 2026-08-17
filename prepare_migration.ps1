[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$script:MissingRequired = 0
$script:Warnings = 0

function Test-GitTracked {
    param([string]$RelativePath)
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return 'UNKNOWN' }
    & git -C $ProjectRoot ls-files --error-unmatch -- $RelativePath *> $null
    if ($LASTEXITCODE -eq 0) { return 'YES' }
    return 'NO'
}

function Test-GitIgnored {
    param([string]$RelativePath)
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return 'UNKNOWN' }
    & git -C $ProjectRoot check-ignore -q -- $RelativePath
    if ($LASTEXITCODE -eq 0) { return 'YES' }
    return 'NO'
}

function Show-MigrationItem {
    param(
        [string]$Path,
        [ValidateSet('REQUIRED', 'RECOMMENDED', 'REGENERATE')]
        [string]$Policy,
        [string]$Purpose
    )
    $fullPath = Join-Path $ProjectRoot $Path
    $exists = Test-Path -LiteralPath $fullPath
    $presence = 'MISSING'
    if ($exists) { $presence = 'OK' }
    $tracked = Test-GitTracked $Path
    $ignored = Test-GitIgnored $Path
    Write-Host ('[{0}] {1}' -f $presence, $Path)
    Write-Host ('     Policy={0} GitTracked={1} GitIgnored={2}' -f $Policy, $tracked, $ignored)
    Write-Host ('     Purpose={0}' -f $Purpose)
    if (-not $exists -and $Policy -eq 'REQUIRED') { $script:MissingRequired++ }
    if (-not $exists -and $Policy -eq 'RECOMMENDED') { $script:Warnings++ }
}

Write-Host '=== Python Drive Organizer Migration Readiness ==='
Write-Host ('Project root: {0}' -f $ProjectRoot)
Write-Host 'Secret values are never displayed.'
Write-Host ''

Show-MigrationItem 'requirements.txt' REQUIRED 'Rebuild the Python environment.'
Show-MigrationItem 'setup_windows.ps1' REQUIRED 'Install and register the project on the new PC.'
Show-MigrationItem 'verify_install.ps1' REQUIRED 'Run non-destructive verification.'
Show-MigrationItem 'MANUAL_ONLINE_SETUP.md' REQUIRED 'Complete Google, Cloudflare, and GPT manual steps.'
Show-MigrationItem 'MIGRATION_GUIDE.md' REQUIRED 'Follow the end-to-end migration procedure.'
Show-MigrationItem '.env' REQUIRED 'Preserve or regenerate the FastAPI PDO_API_KEY.'
Show-MigrationItem 'credentials.json' REQUIRED 'OAuth client configuration; always place manually.'
Show-MigrationItem 'token.json' RECOMMENDED 'Drive metadata OAuth token; safe copy or regenerate.'
Show-MigrationItem 'drive_download_token.json' RECOMMENDED 'Drive download/read OAuth token; safe copy or regenerate.'
Show-MigrationItem 'gmail_send_token.json' RECOMMENDED 'Gmail send OAuth token; safe copy or regenerate.'
Show-MigrationItem 'drive_share_token.json' RECOMMENDED 'Drive sharing OAuth token; safe copy or regenerate.'
Show-MigrationItem 'data\drive_index.db' RECOMMENDED 'Faster recovery; otherwise Daily Refresh can regenerate it.'
Show-MigrationItem 'data\email_send_state.db' RECOMMENDED 'Preserves legacy email idempotency and duplicate-send history.'
Show-MigrationItem 'data\enhanced_email_state.db' RECOMMENDED 'Preserves enhanced email preview/send/idempotency history.'
Show-MigrationItem '.venv' REGENERATE 'Do not copy; setup creates a new PC-local virtual environment.'
Show-MigrationItem 'logs' REGENERATE 'Optional history only; setup recreates the directory.'

Write-Host ''
if (Get-Command git -ErrorAction SilentlyContinue) {
    $statusLines = @(& git -C $ProjectRoot status --porcelain)
    if ($statusLines.Count -eq 0 -or ($statusLines.Count -eq 1 -and -not $statusLines[0])) {
        Write-Host '[OK] Git working tree is clean.'
    } else {
        Write-Host ('[WARNING] Git working tree has {0} changed or untracked entries. Commit or include them in the private backup.' -f $statusLines.Count)
        $script:Warnings++
    }
} else {
    Write-Host '[WARNING] Git is unavailable; tracked-state checks are incomplete.'
    $script:Warnings++
}

Write-Host ''
Write-Host 'Backup classification:'
Write-Host '- Git tracked: clone/pull from the verified remote after committing all intended changes.'
Write-Host '- Git ignored + REQUIRED/RECOMMENDED: copy separately using an encrypted local device or approved secure storage.'
Write-Host '- REGENERATE: do not migrate; create again on the new PC.'
Write-Host '- No PRIVATE_MIGRATION_BUNDLE is generated automatically.'
Write-Host ''
Write-Host ('Summary: missing_required={0} warnings={1}' -f $script:MissingRequired, $script:Warnings)
if ($script:MissingRequired -gt 0) { exit 1 }
exit 0
