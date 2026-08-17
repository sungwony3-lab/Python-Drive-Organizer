[CmdletBinding()]
param(
    [string]$PublicHealthUrl = 'https://drive-api.sungwony.pe.kr/health',
    [switch]$SkipPublicHealth,
    [switch]$SkipUnitTests
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$script:Counts = @{ PASS = 0; WARNING = 0; FAIL = 0; MANUAL = 0 }

function Write-VerifyStatus {
    param(
        [ValidateSet('PASS', 'WARNING', 'FAIL', 'MANUAL')]
        [string]$Status,
        [string]$Message
    )
    $label = $Status
    if ($Status -eq 'MANUAL') { $label = 'MANUAL ACTION REQUIRED' }
    Write-Host ('[{0}] {1}' -f $label, $Message)
    $script:Counts[$Status]++
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)\s*$')) {
            $value = $Matches[1].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(2000)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-ProjectTask {
    param(
        [string]$Name,
        [string]$ExpectedArguments,
        [string]$ExpectedTriggerClass,
        [string]$ExpectedStart,
        [string]$ExpectedDelay
    )
    try {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
        $action = $task.Actions[0]
        $expectedPython = [System.IO.Path]::GetFullPath($VenvPython)
        $actionMatches = (
            [string]::Equals([System.IO.Path]::GetFullPath($action.Execute), $expectedPython, [System.StringComparison]::OrdinalIgnoreCase) -and
            $action.Arguments -eq $ExpectedArguments -and
            [string]::Equals([System.IO.Path]::GetFullPath($action.WorkingDirectory), $ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)
        )
        $settingsMatch = $task.Settings.MultipleInstances -eq 'IgnoreNew' -and $task.Settings.StartWhenAvailable
        $trigger = $task.Triggers[0]
        $triggerMatches = $trigger.CimClass.CimClassName -eq $ExpectedTriggerClass
        if ($ExpectedStart -and $trigger.StartBoundary) {
            $triggerMatches = $triggerMatches -and ([datetime]$trigger.StartBoundary).ToString('HH:mm') -eq $ExpectedStart
        }
        if ($ExpectedDelay) {
            $triggerMatches = $triggerMatches -and $trigger.Delay -eq $ExpectedDelay
        }
        $principalMatches = $task.Principal.LogonType -eq 'Interactive' -and $task.Principal.RunLevel -eq 'Limited'
        if ($actionMatches -and $settingsMatch -and $triggerMatches -and $principalMatches) {
            Write-VerifyStatus PASS ('Task definition verified: {0} (state={1})' -f $Name, $task.State)
        } else {
            Write-VerifyStatus FAIL ('Task definition differs from the project contract: {0}' -f $Name)
        }
    } catch {
        Write-VerifyStatus FAIL ('Task missing or unreadable: {0}' -f $Name)
    }
}

Write-Host '=== Python Drive Organizer Install Verification ==='
Write-Host ('Project root: {0}' -f $ProjectRoot)

if ($env:OS -eq 'Windows_NT') {
    Write-VerifyStatus PASS ('Windows detected: {0}' -f [System.Environment]::OSVersion.VersionString)
} else {
    Write-VerifyStatus FAIL 'This verification targets Windows.'
}

if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    try {
        $pythonInfo = & $VenvPython -c 'import os,sys; print("|".join((os.path.abspath(sys.executable),".".join(map(str,sys.version_info[:3])),os.path.abspath(sys.prefix))))'
        $parts = ([string]$pythonInfo).Trim().Split('|')
        $expectedPrefix = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot '.venv'))
        if ($parts.Count -eq 3 -and [string]::Equals($parts[2], $expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-VerifyStatus PASS ('Project .venv Python {0} is active.' -f $parts[1])
        } else {
            Write-VerifyStatus FAIL '.venv Python prefix does not match this project path.'
        }
    } catch {
        Write-VerifyStatus FAIL '.venv Python could not be executed.'
    }
} else {
    Write-VerifyStatus FAIL '.venv\Scripts\python.exe is missing.'
}

if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    & $VenvPython -m pip check
    if ($LASTEXITCODE -eq 0) {
        Write-VerifyStatus PASS 'pip check passed.'
    } else {
        Write-VerifyStatus FAIL 'pip check failed.'
    }

    & $VenvPython -c 'import fastapi,httpx,pydantic,uvicorn,dotenv,google.auth,googleapiclient,google_auth_oauthlib,docx,openpyxl; import api_server,daily_refresh,email_service,enhanced_email_service,gmail_client,drive_share_client,tree_export_service; print("Imports passed.")'
    if ($LASTEXITCODE -eq 0) {
        Write-VerifyStatus PASS 'Runtime, Daily Refresh, API, and email imports passed.'
    } else {
        Write-VerifyStatus FAIL 'One or more required imports failed.'
    }
}

$secretRoles = [ordered]@{
    '.env' = 'FastAPI Bearer authentication'
    'credentials.json' = 'Google OAuth client'
    'token.json' = 'Drive metadata index'
    'drive_download_token.json' = 'Drive download/read'
    'gmail_send_token.json' = 'Gmail send'
    'drive_share_token.json' = 'Drive link sharing'
}
foreach ($name in $secretRoles.Keys) {
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot $name) -PathType Leaf) {
        Write-VerifyStatus PASS ('Secret file present: {0} ({1}); value not displayed.' -f $name, $secretRoles[$name])
    } else {
        Write-VerifyStatus MANUAL ('Missing {0}; required for {1}.' -f $name, $secretRoles[$name])
    }
}

$databasePolicies = [ordered]@{
    'data\drive_index.db' = 'Drive index; regeneratable by Daily Refresh'
    'data\email_send_state.db' = 'legacy email idempotency history; migration recommended'
    'data\enhanced_email_state.db' = 'enhanced preview/send/idempotency history; migration strongly recommended'
}
foreach ($relativePath in $databasePolicies.Keys) {
    $path = Join-Path $ProjectRoot $relativePath
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Write-VerifyStatus PASS ('Database present: {0} ({1})' -f $relativePath, $databasePolicies[$relativePath])
    } else {
        Write-VerifyStatus WARNING ('Database missing: {0} ({1})' -f $relativePath, $databasePolicies[$relativePath])
    }
}

$driveDatabase = Join-Path $ProjectRoot 'data\drive_index.db'
if ((Test-Path -LiteralPath $VenvPython -PathType Leaf) -and (Test-Path -LiteralPath $driveDatabase -PathType Leaf)) {
    $quickCheck = & $VenvPython -c 'import sqlite3,sys; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); print(c.execute("PRAGMA quick_check").fetchone()[0]); c.close()' $driveDatabase
    if ($LASTEXITCODE -eq 0 -and ([string]$quickCheck).Trim() -eq 'ok') {
        Write-VerifyStatus PASS 'drive_index.db read-only PRAGMA quick_check passed.'
    } else {
        Write-VerifyStatus FAIL 'drive_index.db integrity check failed.'
    }
}

if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'daily_refresh.py') -PathType Leaf) {
    Write-VerifyStatus PASS 'Daily Refresh entrypoint exists: daily_refresh.py'
} else {
    Write-VerifyStatus FAIL 'daily_refresh.py is missing.'
}

Test-ProjectTask `
    -Name 'Python Drive Organizer API' `
    -ExpectedArguments '-m uvicorn api_server:app --host 127.0.0.1 --port 8000' `
    -ExpectedTriggerClass 'MSFT_TaskLogonTrigger' `
    -ExpectedStart '' `
    -ExpectedDelay 'PT20S'
Test-ProjectTask `
    -Name 'Python Drive Organizer Daily Refresh' `
    -ExpectedArguments 'daily_refresh.py' `
    -ExpectedTriggerClass 'MSFT_TaskDailyTrigger' `
    -ExpectedStart '08:00' `
    -ExpectedDelay ''

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($cloudflared) {
    $version = & $cloudflared.Source --version 2>&1
    Write-VerifyStatus PASS ('cloudflared binary found: {0}' -f ([string]$version).Trim())
} else {
    Write-VerifyStatus MANUAL 'cloudflared binary is missing.'
}
$cloudflaredService = Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue
if ($cloudflaredService -and $cloudflaredService.Status -eq 'Running' -and $cloudflaredService.StartType -eq 'Automatic') {
    Write-VerifyStatus PASS 'cloudflared service is Running / Automatic.'
} elseif ($cloudflaredService) {
    Write-VerifyStatus MANUAL ('cloudflared service state is {0} / {1}.' -f $cloudflaredService.Status, $cloudflaredService.StartType)
} else {
    Write-VerifyStatus MANUAL 'cloudflared Windows service is not registered.'
}

if (Test-TcpPort -HostName '127.0.0.1' -Port 8000) {
    Write-VerifyStatus PASS 'TCP port 8000 is listening on localhost.'
} else {
    Write-VerifyStatus FAIL 'TCP port 8000 is not listening on localhost.'
}

try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5
    if ($health.status -eq 'ok') {
        Write-VerifyStatus PASS 'localhost /health returned ok.'
    } else {
        Write-VerifyStatus FAIL 'localhost /health returned an unexpected response.'
    }
} catch {
    Write-VerifyStatus FAIL 'localhost /health request failed.'
}

if ($SkipPublicHealth) {
    Write-VerifyStatus WARNING 'Public HTTPS health check skipped by option.'
} else {
    try {
        $publicHealth = Invoke-RestMethod -Uri $PublicHealthUrl -TimeoutSec 15
        if ($publicHealth.status -eq 'ok') {
            Write-VerifyStatus PASS ('Public HTTPS health returned ok: {0}' -f $PublicHealthUrl)
        } else {
            Write-VerifyStatus WARNING 'Public health returned an unexpected response.'
        }
    } catch {
        Write-VerifyStatus MANUAL 'Public health is unavailable; complete or review the Cloudflare connector.'
    }
}

$apiKey = Get-DotEnvValue -Path (Join-Path $ProjectRoot '.env') -Name 'PDO_API_KEY'
if ($apiKey -and $apiKey.Length -ge 32) {
    try {
        $headers = @{ Authorization = 'Bearer ' + $apiKey }
        $statusResponse = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/status' -Headers $headers -TimeoutSec 5
        if ($statusResponse -and $null -ne $statusResponse.files_count -and $null -ne $statusResponse.folders_count) {
            Write-VerifyStatus PASS 'Bearer-authenticated /status and database counts succeeded; credential value not displayed.'
        } else {
            Write-VerifyStatus FAIL 'Bearer /status returned no response.'
        }
    } catch {
        Write-VerifyStatus FAIL 'Bearer-authenticated /status failed.'
    } finally {
        $apiKey = $null
        $headers = $null
    }
} else {
    Write-VerifyStatus MANUAL '.env must contain a PDO_API_KEY of at least 32 characters before Bearer /status verification.'
}

if ($SkipUnitTests) {
    Write-VerifyStatus WARNING 'Unit tests skipped by option.'
} elseif (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    & $VenvPython -m unittest discover
    if ($LASTEXITCODE -eq 0) {
        Write-VerifyStatus PASS 'Unit tests passed.'
    } else {
        Write-VerifyStatus FAIL 'Unit tests failed.'
    }
}

Write-Host ''
Write-Host '=== Verification summary ==='
Write-Host ('PASS={0} WARNING={1} FAIL={2} MANUAL_ACTION_REQUIRED={3}' -f `
    $script:Counts.PASS, $script:Counts.WARNING, $script:Counts.FAIL, $script:Counts.MANUAL)
Write-Host 'No email was sent and no Google Drive permission or file was changed.'
if ($script:Counts.FAIL -gt 0) { exit 1 }
exit 0
