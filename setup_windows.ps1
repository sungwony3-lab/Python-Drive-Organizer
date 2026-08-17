[CmdletBinding()]
param(
    [string]$PythonExecutable,
    [switch]$InstallPython,
    [switch]$InstallCloudflared,
    [switch]$ReplaceExistingTasks,
    [switch]$SkipUnitTests,
    [switch]$SkipTaskRegistration
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$LogDirectory = Join-Path $ProjectRoot 'logs'
$LogPath = Join-Path $LogDirectory 'setup.log'
$script:Counts = @{ PASS = 0; WARNING = 0; FAIL = 0; MANUAL = 0 }
$script:Fatal = $false

function Protect-LogMessage {
    param([string]$Message)
    if ($null -eq $Message) { return '' }
    $safe = $Message -replace '(?i)(authorization\s*:\s*bearer\s+)\S+', '$1[REDACTED]'
    $safe = $safe -replace '(?i)(PDO_API_KEY\s*[=:]\s*)\S+', '$1[REDACTED]'
    $safe = $safe -replace '(?i)((?:access|refresh|cloudflare)[_-]?token\s*[=:]\s*)\S+', '$1[REDACTED]'
    $safe = $safe -replace '(?i)("(?:client_secret|refresh_token|access_token)"\s*:\s*")[^"]+', '$1[REDACTED]'
    return $safe.Replace("`r", ' ').Replace("`n", ' ')
}

function Write-SetupStatus {
    param(
        [ValidateSet('PASS', 'WARNING', 'FAIL', 'MANUAL')]
        [string]$Status,
        [string]$Message
    )
    $safe = Protect-LogMessage $Message
    $label = $Status
    if ($Status -eq 'MANUAL') { $label = 'MANUAL ACTION REQUIRED' }
    $line = '[{0}] {1}' -f $label, $safe
    Write-Host $line
    if (Test-Path -LiteralPath $LogDirectory) {
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    }
    $script:Counts[$Status]++
}

function Write-SetupSummary {
    Write-Host ''
    Write-Host '=== Windows setup summary ==='
    Write-Host ('PASS={0} WARNING={1} FAIL={2} MANUAL_ACTION_REQUIRED={3}' -f `
        $script:Counts.PASS, $script:Counts.WARNING, $script:Counts.FAIL, $script:Counts.MANUAL)
    Write-Host ('Project root: {0}' -f $ProjectRoot)
    Write-Host ('Log: {0}' -f $LogPath)
}

function Stop-SetupIfFatal {
    if ($script:Fatal) {
        Write-SetupSummary
        exit 1
    }
}

function Resolve-PythonExecutable {
    param([string]$RequestedPath)
    $candidates = @()
    if ($RequestedPath) {
        $candidates += ,@($RequestedPath)
    }
    foreach ($name in @('python', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += ,@($command.Source) }
    }
    $launcher = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($launcher) { $candidates += ,@($launcher.Source, '-3') }

    foreach ($candidate in $candidates) {
        try {
            $commandPath = $candidate[0]
            $prefixArguments = @()
            if ($candidate.Count -gt 1) { $prefixArguments = $candidate[1..($candidate.Count - 1)] }
            $resolved = & $commandPath @prefixArguments -c 'import os,sys; print(os.path.abspath(sys.executable))' 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) {
                return ([string]$resolved).Trim()
            }
        } catch {
            continue
        }
    }
    return $null
}

function Test-PythonVersion {
    param([string]$Executable)
    try {
        $versionText = & $Executable -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
        if ($LASTEXITCODE -ne 0) { return $false }
        $version = [version]([string]$versionText).Trim()
        if ($version.Major -lt 3 -or ($version.Major -eq 3 -and $version.Minor -lt 10) -or $version.Major -ge 4) {
            Write-SetupStatus FAIL ('Python {0} is unsupported; use Python 3.10 or newer, below 4.0.' -f $version)
            return $false
        }
        Write-SetupStatus PASS ('Python {0} found at a discovered executable path.' -f $version)
        return $true
    } catch {
        Write-SetupStatus FAIL 'Python version could not be validated.'
        return $false
    }
}

function Test-TaskMatches {
    param(
        $Task,
        [string]$ExpectedExecute,
        [string]$ExpectedArguments,
        [string]$ExpectedWorkingDirectory
    )
    if ($null -eq $Task -or $Task.Actions.Count -ne 1) { return $false }
    $action = $Task.Actions[0]
    return (
        [string]::Equals([System.IO.Path]::GetFullPath($action.Execute), $ExpectedExecute, [System.StringComparison]::OrdinalIgnoreCase) -and
        $action.Arguments -eq $ExpectedArguments -and
        [string]::Equals([System.IO.Path]::GetFullPath($action.WorkingDirectory), $ExpectedWorkingDirectory, [System.StringComparison]::OrdinalIgnoreCase) -and
        $Task.Settings.MultipleInstances -eq 'IgnoreNew' -and
        $Task.Settings.StartWhenAvailable
    )
}

function Install-OrValidateTask {
    param(
        [string]$Name,
        [string]$Arguments,
        $Trigger,
        [string]$ExpectedTriggerType,
        [string]$ExpectedStartTime,
        [string]$ExpectedDelay
    )
    try {
        $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    } catch {
        Write-SetupStatus MANUAL ('Task Scheduler access failed for {0}; run PowerShell with the required Windows permission.' -f $Name)
        return $false
    }

    $venvPython = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot '.venv\Scripts\python.exe'))
    $matches = Test-TaskMatches $existing $venvPython $Arguments $ProjectRoot
    if ($matches) {
        $existingTrigger = $existing.Triggers[0]
        $triggerMatches = $existingTrigger.CimClass.CimClassName -eq $ExpectedTriggerType
        if ($ExpectedStartTime) {
            $triggerMatches = $triggerMatches -and $existingTrigger.StartBoundary -and `
                ([datetime]$existingTrigger.StartBoundary).ToString('HH:mm') -eq $ExpectedStartTime
        }
        if ($ExpectedDelay) {
            $triggerMatches = $triggerMatches -and $existingTrigger.Delay -eq $ExpectedDelay
        }
        $principalMatches = $existing.Principal.LogonType -eq 'Interactive' -and `
            $existing.Principal.RunLevel -eq 'Limited'
        if ($triggerMatches -and $principalMatches) {
            Write-SetupStatus PASS ('Task already matches: {0}' -f $Name)
            return $true
        }
    }
    if ($existing -and -not $ReplaceExistingTasks) {
        Write-SetupStatus MANUAL ('Task exists with different settings: {0}. Re-run with -ReplaceExistingTasks after review.' -f $Name)
        return $false
    }

    try {
        $action = New-ScheduledTaskAction -Execute $venvPython -Argument $Arguments -WorkingDirectory $ProjectRoot
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
        $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -Principal $principal -Force | Out-Null
        Write-SetupStatus PASS ('Task registered: {0}' -f $Name)
        return $true
    } catch {
        Write-SetupStatus FAIL ('Task registration failed for {0}: {1}' -f $Name, $_.Exception.Message)
        return $false
    }
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
Set-Content -LiteralPath $LogPath -Value ('Setup started {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')) -Encoding UTF8
Write-Host '=== Python Drive Organizer Windows Setup ==='
Write-Host ('Project root: {0}' -f $ProjectRoot)

if ($env:OS -eq 'Windows_NT') {
    Write-SetupStatus PASS ('Windows detected: {0}' -f [System.Environment]::OSVersion.VersionString)
} else {
    Write-SetupStatus FAIL 'This setup script supports Windows only.'
    $script:Fatal = $true
}

if ($PSVersionTable.PSVersion -ge [version]'5.1') {
    Write-SetupStatus PASS ('PowerShell {0}' -f $PSVersionTable.PSVersion)
} else {
    Write-SetupStatus FAIL 'PowerShell 5.1 or newer is required.'
    $script:Fatal = $true
}

$requiredProjectFiles = @(
    'requirements.txt', 'api_server.py', 'daily_refresh.py', 'main.py',
    'database.py', 'tree_export_service.py', 'setup_windows.ps1',
    'verify_install.ps1'
)
foreach ($relativePath in $requiredProjectFiles) {
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot $relativePath) -PathType Leaf) {
        Write-SetupStatus PASS ('Project file found: {0}' -f $relativePath)
    } else {
        Write-SetupStatus FAIL ('Required project file is missing: {0}' -f $relativePath)
        $script:Fatal = $true
    }
}
Stop-SetupIfFatal

$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($gitCommand) {
    $gitVersion = & $gitCommand.Source --version
    Write-SetupStatus PASS ([string]$gitVersion)
} else {
    Write-SetupStatus WARNING 'Git is not installed. A copied project can run, but clone/update commands will be unavailable.'
}

$systemPython = Resolve-PythonExecutable $PythonExecutable
if (-not $systemPython -and $InstallPython) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-SetupStatus FAIL 'winget is unavailable, so Python cannot be installed automatically.'
        $script:Fatal = $true
    } else {
        Write-SetupStatus WARNING 'Installing Python 3.14 from the official winget package.'
        & $winget.Source install --id Python.Python.3.14 --exact --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-SetupStatus FAIL 'Python installation failed.'
            $script:Fatal = $true
        } else {
            $systemPython = Resolve-PythonExecutable $null
        }
    }
}
if (-not $systemPython) {
    Write-SetupStatus MANUAL 'Install Python 3.10 or newer from python.org, or re-run with -InstallPython.'
    $script:Fatal = $true
} elseif (-not (Test-PythonVersion $systemPython)) {
    $script:Fatal = $true
}
Stop-SetupIfFatal

foreach ($directory in @('data', 'logs')) {
    $path = Join-Path $ProjectRoot $directory
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    Write-SetupStatus PASS ('Directory ready: {0}/' -f $directory)
}

$ignoreFile = Join-Path $ProjectRoot '.gitignore'
$requiredIgnoreEntries = @(
    '.venv/', '.env', 'credentials.json', 'token.json',
    'drive_download_token.json', 'gmail_send_token.json',
    'drive_share_token.json', 'data/', 'logs/'
)
if (Test-Path -LiteralPath $ignoreFile) {
    $ignoreLines = Get-Content -LiteralPath $ignoreFile
    foreach ($entry in $requiredIgnoreEntries) {
        if ($ignoreLines -contains $entry) {
            Write-SetupStatus PASS ('.gitignore protects {0}' -f $entry)
        } else {
            Write-SetupStatus WARNING ('.gitignore is missing expected entry: {0}' -f $entry)
        }
    }
} else {
    Write-SetupStatus FAIL '.gitignore is missing.'
    $script:Fatal = $true
}
Stop-SetupIfFatal

$secretRoles = [ordered]@{
    '.env' = 'FastAPI Bearer PDO_API_KEY'
    'credentials.json' = 'Google OAuth client configuration'
    'token.json' = 'Drive metadata index read'
    'drive_download_token.json' = 'Drive file/download read'
    'gmail_send_token.json' = 'Gmail send'
    'drive_share_token.json' = 'Drive anyone/reader permission create'
}
foreach ($name in $secretRoles.Keys) {
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot $name) -PathType Leaf) {
        Write-SetupStatus PASS ('Secret file present: {0} ({1}); value not displayed.' -f $name, $secretRoles[$name])
    } else {
        Write-SetupStatus MANUAL ('Place {0} manually for: {1}.' -f $name, $secretRoles[$name])
    }
}

$venvDirectory = Join-Path $ProjectRoot '.venv'
$venvPython = Join-Path $venvDirectory 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvDirectory)) {
    Write-SetupStatus WARNING 'Creating a new local .venv. An old-PC .venv is never copied or reused.'
    & $systemPython -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0) {
        Write-SetupStatus FAIL '.venv creation failed.'
        $script:Fatal = $true
    } else {
        Write-SetupStatus PASS '.venv created.'
    }
} elseif (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-SetupStatus FAIL 'Existing .venv is not runnable. Rename or remove only this .venv, then run setup again.'
    $script:Fatal = $true
} else {
    try {
        $venvPrefix = & $venvPython -c 'import os,sys; print(os.path.abspath(sys.prefix))'
        if ($LASTEXITCODE -ne 0 -or -not [string]::Equals(([string]$venvPrefix).Trim(), [System.IO.Path]::GetFullPath($venvDirectory), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'venv prefix mismatch'
        }
        Write-SetupStatus PASS 'Existing local .venv is valid and will be reused on this PC.'
    } catch {
        Write-SetupStatus FAIL 'Existing .venv appears copied or broken. Rename or remove only this .venv, then run setup again.'
        $script:Fatal = $true
    }
}
Stop-SetupIfFatal

Write-Host 'Installing requirements into the project .venv...'
& $venvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-SetupStatus FAIL 'requirements.txt installation failed.'
    $script:Fatal = $true
} else {
    Write-SetupStatus PASS 'requirements.txt installed with the .venv Python.'
}
Stop-SetupIfFatal

& $venvPython -m pip check
if ($LASTEXITCODE -eq 0) {
    Write-SetupStatus PASS 'pip check passed.'
} else {
    Write-SetupStatus FAIL 'pip check found broken or conflicting dependencies.'
    $script:Fatal = $true
}

& $venvPython -c 'import fastapi,httpx,pydantic,uvicorn,dotenv,google.auth,googleapiclient,google_auth_oauthlib,docx,openpyxl; import api_server,daily_refresh,email_service,enhanced_email_service,gmail_client,drive_share_client,tree_export_service; print("Required imports passed.")'
if ($LASTEXITCODE -eq 0) {
    Write-SetupStatus PASS 'Runtime and email module imports passed.'
} else {
    Write-SetupStatus FAIL 'Required module import failed.'
    $script:Fatal = $true
}
Stop-SetupIfFatal

$apiTaskReady = $false
if ($SkipTaskRegistration) {
    Write-SetupStatus WARNING 'Task registration skipped by option.'
} else {
    $apiTrigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $apiTrigger.Delay = 'PT20S'
    $apiTaskReady = Install-OrValidateTask `
        -Name 'Python Drive Organizer API' `
        -Arguments '-m uvicorn api_server:app --host 127.0.0.1 --port 8000' `
        -Trigger $apiTrigger `
        -ExpectedTriggerType 'MSFT_TaskLogonTrigger' `
        -ExpectedStartTime '' `
        -ExpectedDelay 'PT20S'

    $dailyTrigger = New-ScheduledTaskTrigger -Daily -At '08:00'
    [void](Install-OrValidateTask `
        -Name 'Python Drive Organizer Daily Refresh' `
        -Arguments 'daily_refresh.py' `
        -Trigger $dailyTrigger `
        -ExpectedTriggerType 'MSFT_TaskDailyTrigger' `
        -ExpectedStartTime '08:00' `
        -ExpectedDelay '')
}

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared -and $InstallCloudflared) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-SetupStatus WARNING 'Installing cloudflared binary from winget. Tunnel service registration remains manual.'
        & $winget.Source install --id Cloudflare.cloudflared --exact --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { $cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue }
    }
}
if ($cloudflared) {
    $cloudflaredVersion = & $cloudflared.Source --version 2>&1
    Write-SetupStatus PASS ('cloudflared installed: {0}' -f ([string]$cloudflaredVersion).Trim())
} else {
    Write-SetupStatus MANUAL 'Install cloudflared, or re-run with -InstallCloudflared.'
}

$cloudflaredService = Get-Service -Name 'cloudflared' -ErrorAction SilentlyContinue
if ($cloudflaredService) {
    if ($cloudflaredService.Status -eq 'Running' -and $cloudflaredService.StartType -eq 'Automatic') {
        Write-SetupStatus PASS 'cloudflared Windows service is Running / Automatic.'
    } else {
        Write-SetupStatus MANUAL ('cloudflared service exists but is {0} / {1}; review connector setup.' -f $cloudflaredService.Status, $cloudflaredService.StartType)
    }
} else {
    Write-SetupStatus MANUAL 'cloudflared Windows service is not registered. Complete the connector step in MANUAL_ONLINE_SETUP.md.'
}

if ($apiTaskReady -and (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) {
    try {
        $task = Get-ScheduledTask -TaskName 'Python Drive Organizer API'
        if ($task.State -ne 'Running') {
            Start-ScheduledTask -TaskName 'Python Drive Organizer API'
        }
        $healthy = $false
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            Start-Sleep -Seconds 1
            try {
                $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
                if ($health.status -eq 'ok') { $healthy = $true; break }
            } catch { }
        }
        if ($healthy) {
            Write-SetupStatus PASS 'localhost FastAPI /health returned ok.'
        } else {
            Write-SetupStatus WARNING 'localhost /health did not become ready. Run verify_install.ps1 after checking .env and drive_index.db.'
        }
    } catch {
        Write-SetupStatus WARNING 'FastAPI task could not be started for localhost health verification.'
    }
} else {
    Write-SetupStatus MANUAL 'localhost health verification requires the API task and a valid .env.'
}

if ($SkipUnitTests) {
    Write-SetupStatus WARNING 'Unit tests skipped by option.'
} else {
    & $venvPython -m unittest discover
    if ($LASTEXITCODE -eq 0) {
        Write-SetupStatus PASS 'Unit tests passed.'
    } else {
        Write-SetupStatus FAIL 'Unit tests failed.'
        $script:Fatal = $true
    }
}

Write-SetupSummary
if ($script:Fatal) { exit 1 }
exit 0
