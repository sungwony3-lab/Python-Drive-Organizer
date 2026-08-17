[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [switch]$ConfirmRemoval
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)

if (-not $ConfirmRemoval) {
    Write-Host '[MANUAL ACTION REQUIRED] No task was removed.'
    Write-Host 'Re-run with -ConfirmRemoval to remove only the two Python Drive Organizer scheduled tasks.'
    exit 2
}

$taskNames = @(
    'Python Drive Organizer API',
    'Python Drive Organizer Daily Refresh'
)

foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host ('[PASS] Task already absent: {0}' -f $taskName)
        continue
    }
    if ($PSCmdlet.ShouldProcess($taskName, 'Unregister scheduled task')) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host ('[PASS] Removed scheduled task: {0}' -f $taskName)
    }
}

Write-Host 'Project files, SQLite databases, OAuth tokens, .env, and the cloudflared service were not changed.'
