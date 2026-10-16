<#
    PuffPAI BuildBot installer (Windows, PowerShell 5.1+).

    Most people never run this file directly: they double-click install.cmd,
    which runs it with -ExecutionPolicy Bypass for that one window.

    It makes a venv in .\venv, installs this checkout with pip, builds the web
    page (the dashboard), and writes double-click launchers. It never installs
    anything (Python, Node, Ollama) without a plain yes from the person at
    the keyboard, and never downloads a model unless -Pull asks for one.

    ASCII only in every console string: the legacy Windows console garbles
    em dashes and curly quotes. Everything a person reads here is plain words;
    internal names stay in comments.

    Usage:
        install.cmd [options]                      (double-click, or from cmd)
        powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 [options]

    Test hook (not for owners): PUFFPAI_INSTALL_PRETEND_MISSING=python,node,
    ollama,winget makes those tools look missing, so the offer-to-install path
    can be exercised with -DryRun on a machine that already has them.
#>

[CmdletBinding()]
param(
    [switch]$WithDashboard,     # kept so old command lines work; now the default
    [switch]$NoDashboard,
    [switch]$WithVoice,
    [switch]$WithDev,
    [switch]$YesInstallOllama,
    [string]$Pull = "",
    [int]$Port = 8000,
    [int]$DashboardPort = 3003,
    [string]$StateDir = "",
    [switch]$NoQuestions,
    [switch]$NewBot,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# The web page's build tool (Next.js) sends usage reports to its maker unless
# this is set. BuildBot promises zero telemetry, so it is off for everything
# this installer runs, and every launcher and env file below sets it again.
$env:NEXT_TELEMETRY_DISABLED = "1"

$PyMinMajor   = 3
$PyMinMinor   = 11       # setup.py: python_requires=">=3.11"
# Node.js: 22.4 or newer, because the part that lets your bot use a browser app
# needs Node's built-in WebSocket. The web page itself
# (dashboard/package.json engines: ^20.19 || >=22.12) needs 22.12 on the 22
# line, so the check below asks for both: 22.12 or newer.
$NodeMinMajor = 22
$NodeMinMinor = 4
$NodeWebPageMinor = 12

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RepoDir "venv"
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
$BuildDashboard = -not $NoDashboard

# Everything this window shows is also kept in install-log.txt next to this
# file, so a window that closes still leaves the reason behind.
$InstallLog = Join-Path $RepoDir "install-log.txt"
if (-not $DryRun) { try { Start-Transcript -Path $InstallLog -Force | Out-Null } catch { } }

# install.cmd waits for a key at the end. Started any other way (for example
# "Run with PowerShell"), the window would close at once, so wait here.
function Pause-IfAlone {
    if ($env:PUFFPAI_FROM_CMD -or $env:PUFFPAI_NO_PAUSE -or $DryRun) { return }
    try { if ([Console]::IsInputRedirected) { return } } catch { return }
    Write-Host ("The steps above are also in " + $InstallLog + ".")
    try { [void](Read-Host "Press Enter to close this window") } catch { }
}

trap {
    Write-Host ""
    Write-Host ("Setup stopped: " + $_.Exception.Message)
    try { Stop-Transcript | Out-Null } catch { }
    Pause-IfAlone
    exit 1
}

# Ask only when a person can answer: not under -NoQuestions or -DryRun, and not
# when the input is a pipe or a file (a script, a test, a scheduled task).
$CanAsk = $false
if ((-not $NoQuestions) -and (-not $DryRun) -and [Environment]::UserInteractive) {
    try { $CanAsk = -not [Console]::IsInputRedirected } catch { $CanAsk = $false }
}

$Pretend = @()
if ($env:PUFFPAI_INSTALL_PRETEND_MISSING) {
    $Pretend = $env:PUFFPAI_INSTALL_PRETEND_MISSING.ToLower().Split(",") | ForEach-Object { $_.Trim() }
}
function Pretends ($name) { return ($Pretend -contains $name) }

function Say  ($m) { Write-Host $m }
function Step ($n, $m) { Write-Host ""; Write-Host ("Step " + $n + " of 6: " + $m) }
function Fail ($m) {
    Write-Host ""; Write-Host ("Setup stopped: " + $m)
    try { Stop-Transcript | Out-Null } catch { }
    Pause-IfAlone
    exit 1
}

# Run a command and stop if it fails, or print it under -DryRun.
function Invoke-Step {
    param([string]$Exe, [string[]]$Arguments, [string]$WorkDir = $RepoDir)
    $line = "$Exe $($Arguments -join ' ')"
    if ($DryRun) { Say ("  [dry-run] " + $line); return }
    Push-Location $WorkDir
    try {
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { Fail ("this command did not work (code $LASTEXITCODE): " + $line) }
    } finally { Pop-Location }
}

# Run a noisy command with its output going to a log file. On failure, show the
# end of the log so the reason is on screen, not only in the file.
function Invoke-Logged {
    param([string]$CmdLine, [string]$WorkDir, [string]$Log)
    if ($DryRun) { Say ("  [dry-run] " + $CmdLine + "   (output to " + $Log + ")"); return $true }
    $dir = Split-Path -Parent $Log
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Push-Location $WorkDir
    try {
        & cmd.exe /d /s /c ('"' + $CmdLine + ' > "' + $Log + '" 2>&1"')
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    if ($code -ne 0) {
        Say ("  That did not work (code $code). The last lines of " + $Log + ":")
        Get-Content $Log -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Say ("    " + $_) }
        return $false
    }
    return $true
}

function Ask-YesNo ($question) {
    for ($i = 0; $i -lt 3; $i++) {
        $a = (Read-Host ($question + " Type y for yes or n for no")).Trim().ToLower()
        if ($a -in @("y", "yes")) { return $true }
        if ($a -in @("n", "no"))  { return $false }
    }
    return $false
}

# PATH as Windows now has it, so a tool winget just installed is found without
# opening a new window.
function Update-PathFromRegistry {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

# Offer to install one missing tool with winget, after a plain yes. Never
# silently: without a person to ask (and without an explicit flag) it only
# prints how. Returns $true when it installed something.
function Offer-Install {
    param([string]$What, [string]$WingetId, [string]$Page, [bool]$PreApproved = $false)
    $cmd = "winget install --id $WingetId -e --accept-package-agreements --accept-source-agreements"
    $hasWinget = (Get-Command winget -ErrorAction SilentlyContinue) -and -not (Pretends "winget")
    if (-not $hasWinget) {
        Say ("  You can get " + $What + " free from " + $Page)
        Say "  Install it, then run this setup again."
        return $false
    }
    if ($DryRun) {
        Say ("  [dry-run] would ask: Install " + $What + " now? (y/n)")
        Say ("  [dry-run] on yes it would run: " + $cmd)
        return $false
    }
    $yes = $PreApproved
    if (-not $yes) {
        if (-not $CanAsk) {
            Say ("  To install it, open a Command Prompt and type:  " + $cmd)
            Say ("  Or get it from " + $Page)
            return $false
        }
        $yes = Ask-YesNo ("  Install " + $What + " now? It is free, and Windows' own app installer (winget) gets it.")
    }
    if (-not $yes) {
        Say ("  Skipped. You can get it later from " + $Page)
        return $false
    }
    Say ("  Installing " + $What + ". Windows may ask you to allow it.")
    & winget install --id $WingetId -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Say ("  That did not finish (code $LASTEXITCODE). You can get it from " + $Page)
        return $false
    }
    Update-PathFromRegistry
    return $true
}

if ($Help) {
    @"
BuildBot setup for Windows.

Easiest: double-click install.cmd. From a Command Prompt:
    install.cmd [options]

  -NoDashboard        do not build the web page (the terminal still works)
  -WithDashboard      accepted for older instructions; the web page is built
                      anyway unless -NoDashboard is given
  -WithVoice          also install the parts for talking out loud
  -WithDev            also install the tools for running the tests
  -YesInstallOllama   install Ollama with winget without asking first
  -Pull NAME          download this AI model with Ollama at the end
  -Port N             port for your bot (default 8000)
  -DashboardPort N    port for your bot's web page (default 3003)
  -StateDir PATH      where this copy keeps its private files
                      (default: the folder it used last time, else your
                      user folder, AppData\Local\PuffPAI)
  -NoQuestions        ask nothing; print what to do instead (a bot you
                      already have is kept)
  -NewBot             start a new bot even if that folder already has one
                      (it gets a new folder; the old one is left as it is)
  -DryRun             show every step and every file it would write, and
                      change nothing at all
  -Help               this text

Nothing (Python, Node.js, Ollama, an AI model) is installed or
downloaded unless you say yes or pass the flag for it.

This copy gets its own private key, saved in <private files>\buildbot.env
and never printed, so its bot and its web page always recognise each other.
A file .puffpai-install.json beside this script tells every puffpai command
which copy this is; "puffpai where" prints it.
"@ | Write-Host
    exit 0
}

Say ""
Say "BuildBot setup"
Say "This gets BuildBot ready on this computer. Nothing is installed without asking you."
if ($DryRun) { Say "(Practice run: it shows each step and changes nothing.)" }

# --- 1. Python -------------------------------------------------------------
Step 1 "Python (BuildBot is written in it)"

function Find-Python {
    if (Pretends "python") { return $null }
    $probe = "import sys; print(sys.executable); raise SystemExit(0 if sys.version_info[:2] >= ($PyMinMajor, $PyMinMinor) else 1)"
    foreach ($cand in @(@("py", @("-3")), @("python", @()), @("python3", @()))) {
        $exe = $cand[0]; $pre = $cand[1]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            # Under "Continue": a launcher that finds no Python says so on
            # stderr, which under "Stop" would end setup instead of moving on.
            $prevPref = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            # The probe prints the real python.exe. A launcher can print other
            # lines first (a fresh "py" says "Extracting: ..."), so only the
            # last line that names a file counts, and nothing else escapes
            # into what this function returns.
            $out = @()
            try { $out = @(& $exe @pre -c $probe 2>$null); $ok = ($LASTEXITCODE -eq 0) }
            finally { $ErrorActionPreference = $prevPref }
            if ($ok) {
                $path = $out | ForEach-Object { ([string]$_).Trim() } |
                    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -Last 1
                if ($path) { return ,@($path, @()) }
                return ,@($exe, $pre)
            }
        }
    }
    return $null
}

$found = Find-Python
if (-not $found) {
    Say "  Python $PyMinMajor.$PyMinMinor or newer is not on this computer, and BuildBot needs it."
    if (Offer-Install "Python" "Python.Python.3.12" "https://www.python.org/downloads/ (tick 'Add python.exe to PATH')") {
        $found = Find-Python
    }
}
if (-not $found) {
    if ($DryRun) { $found = @("python", @()) }
    else { Fail "BuildBot needs Python $PyMinMajor.$PyMinMinor or newer. Install it, then run this setup again." }
}
$Python = $found[0]; $PythonPre = $found[1]
if (-not $DryRun) {
    $prevPref = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $pyVer = [string](& $Python @PythonPre --version 2>&1) } finally { $ErrorActionPreference = $prevPref }
    Say ("  Found " + $pyVer + ".")
}

# --- 2. venv + pip ---------------------------------------------------------
Step 2 "BuildBot's own parts"
Say "  Getting BuildBot's parts from the Python package library. This takes a few minutes."
if (Test-Path $VenvPy) {
    Say "  Reusing the venv folder from last time."
} else {
    Invoke-Step $Python ($PythonPre + @("-m", "venv", $VenvDir))
}

$extras = @()
if ($WithVoice) { $extras += "voice" }
if ($WithDev)   { $extras += "dev" }
$target = "."
if ($extras.Count -gt 0) { $target = ".[" + ($extras -join ",") + "]" }

Invoke-Step $VenvPy @("-m", "pip", "install", "-q", "--disable-pip-version-check", "--upgrade", "pip")
Invoke-Step $VenvPy @("-m", "pip", "install", "-q", "--disable-pip-version-check", "-e", $target)
if (-not $DryRun) { Say "  Done." }

# --- 3. This install's own key and state dir -------------------------------
#
# Why this section exists: the dashboard proxy sends
# Authorization: Bearer $env:PUFFPAI_API_KEY and nothing else, so if the two
# sides do not hold the SAME key every dashboard call is a 401. Writing a key
# into the state dir is not enough on a machine that has ever run PuffPAI: the
# backend prefers a stored secret (vault or Credential Manager) over that file,
# so a stale entry from another install would win. The one thing that beats
# every store is an explicitly set PUFFPAI_API_KEY, so this install mints a key
# into <state>\buildbot.env and every launcher exports it before starting
# anything. Same value on both sides, stale keyring or not.

Step 3 "Your bot's private files"

function New-ApiKey {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

# Owner-only ACL. icacls needs no administrator rights for a file you own; the
# mode argument of anything POSIX-shaped is ignored by Windows, so this is the
# only way to stop a secret inheriting the directory's ACL. The exit code is
# checked and the result read back: a secret file that could not be made
# owner-only stops the install instead of being written anyway.
function Protect-File ($path) {
    $me = (whoami).Trim()
    # icacls reports failure on stderr; under "Stop" Windows PowerShell would
    # throw on that line before the exit code could be read and explained.
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & icacls $path /inheritance:r /grant:r ("{0}:(R,W)" -f $me) 2>&1 | Out-Null
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prevPref }
    if ($code -ne 0) {
        Fail ("could not make " + $path + " private to your account (icacls exit " + $code + "). Nothing secret was written to it.")
    }
    $acl = Get-Acl -Path $path
    if (-not $acl.AreAccessRulesProtected) {
        Fail ("could not make " + $path + " private to your account (it still takes its folder's permissions). Nothing secret was written to it.")
    }
}

function Get-EnvFileValue ($path, $name) {
    if (-not (Test-Path $path)) { return "" }
    foreach ($line in (Get-Content $path -ErrorAction SilentlyContinue)) {
        if ($line -match ("^\s*" + [regex]::Escape($name) + "\s*=\s*(.*)$")) {
            return $Matches[1].Trim()
        }
    }
    return ""
}

function Resolve-Abs ($path) {
    if ([System.IO.Path]::IsPathRooted($path)) { return [System.IO.Path]::GetFullPath($path) }
    return [System.IO.Path]::GetFullPath((Join-Path $RepoDir $path))
}

$Pointer     = Join-Path $RepoDir ".puffpai-install.json"

# A copy set up with its own folder (-StateDir, or a new bot) is remembered by
# the pointer beside this script. Run again without -StateDir, it carries on
# with that folder instead of going back to the per-user one.
if (($StateDir -eq "") -and (-not $env:PUFFPAI_STATE_DIR) -and (-not $env:PUFFPAI_DATA_DIR) -and
    (Test-Path -LiteralPath $Pointer)) {
    $named = ""
    try { $named = [string]((Get-Content -LiteralPath $Pointer -Raw -ErrorAction Stop | ConvertFrom-Json).env_file) } catch { }
    if ($named -and (Test-Path -LiteralPath $named -PathType Leaf) -and
        ((Get-EnvFileValue $named "PUFFPAI_CHECKOUT") -eq $RepoDir)) {
        $was = Get-EnvFileValue $named "PUFFPAI_STATE_DIR"
        if ($was -and ($was -eq (Get-EnvFileValue $named "PUFFPAI_DATA_DIR"))) { $StateDir = $was }
    }
}
$ChoseState = ($StateDir -ne "") -or [bool]$env:PUFFPAI_STATE_DIR -or [bool]$env:PUFFPAI_DATA_DIR

# Mirrors puffpai.config.get_state_dir so the launcher points where the backend
# already looks: an explicit flag, then PUFFPAI_STATE_DIR, then
# PUFFPAI_DATA_DIR\state, then the per-user location.
# DATA_DIR defaults to the checkout (that is the project's default and the
# manifest lives under it), but -StateDir means "keep this install's whole
# footprint here", so it moves both.
function Resolve-StateDirs {
    if ($StateDir -ne "") {
        $script:State = Resolve-Abs $StateDir
    } elseif ($env:PUFFPAI_STATE_DIR) {
        $script:State = Resolve-Abs $env:PUFFPAI_STATE_DIR
    } elseif ($env:PUFFPAI_DATA_DIR) {
        $script:State = Join-Path (Resolve-Abs $env:PUFFPAI_DATA_DIR) "state"
    } else {
        $base = $env:LOCALAPPDATA
        if (-not $base) { $base = $env:USERPROFILE }
        $script:State = Join-Path $base "PuffPAI"
    }
    if ($env:PUFFPAI_DATA_DIR) {
        $script:DataDir = Resolve-Abs $env:PUFFPAI_DATA_DIR
    } elseif ($StateDir -ne "") {
        $script:DataDir = $script:State
    } else {
        $script:DataDir = $RepoDir
    }
}
Resolve-StateDirs

# Keep the bot already here, or start a new one. A state folder that already
# holds a built bot (a build manifest, a finished setup or a personality) used
# to be reused without a word, so an owner who ran setup again to start over
# never saw the setup questions. Now it asks. A new bot gets a new, empty
# folder beside this script (state, state-2, ...); the old bot's folder is
# never moved, renamed or deleted. -NoQuestions (or nobody at the keyboard)
# keeps the old bot; -NewBot starts a new one.
function Read-JsonFile ($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return (Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json) } catch { return $null }
}
# The first of <install folder>\state, state-2, state-3, ... that is not there
# or is empty.
function Get-NewStateDir {
    for ($n = 1; ; $n++) {
        $p = Join-Path $RepoDir $(if ($n -eq 1) { "state" } else { "state-" + $n })
        if (-not (Test-Path -LiteralPath $p)) { return $p }
        $empty = $false
        try { $empty = (Test-Path -LiteralPath $p -PathType Container) -and (@(Get-ChildItem -LiteralPath $p -Force -ErrorAction Stop).Count -eq 0) } catch { }
        if ($empty) { return $p }
    }
}
$NewState = ""

$ManifestPath = Join-Path $DataDir "config\build_manifest.json"
$PersonaPath  = Join-Path $State "persona.json"
$setupFlag    = Read-JsonFile (Join-Path $DataDir "data\setup_state.json")
$HasBot = (Test-Path -LiteralPath $ManifestPath) -or (Test-Path -LiteralPath $PersonaPath) -or
          ($setupFlag -and $setupFlag.complete)
# This copy's own bot: built files beside this script, not in the shared folder.
$OwnBotHere = ($DataDir -ne $State) -and ((Test-Path -LiteralPath $ManifestPath) -or ($setupFlag -and $setupFlag.complete))
$Decided = $false

# Is somebody else already living there? A manifest or a key file that this
# install did not write means two builds would share one identity. A bot is
# never switched away from without the owner's say: when the copy named in
# the folder is gone (moved or renamed), the bot there is this copy's and is
# kept; when that copy is still on this computer, the owner chooses.
$owner = Get-EnvFileValue (Join-Path $State "buildbot.env") "PUFFPAI_CHECKOUT"
$squatters = @()
foreach ($rel in @("api_key.txt", "config\build_manifest.json", "vault")) {
    if (Test-Path (Join-Path $State $rel)) { $squatters += $rel }
}
$otherCopy = $owner -and ($owner -ne $RepoDir) -and
             (Test-Path -LiteralPath (Join-Path $owner "install.ps1") -PathType Leaf) -and
             (Test-Path -LiteralPath (Join-Path $owner "src\puffpai") -PathType Container)
if ($squatters.Count -gt 0 -and $otherCopy -and -not $OwnBotHere) {
    $Decided = $true
    $ans = ""
    if ($NewBot.IsPresent) { $ans = "o" }
    elseif ($CanAsk -and -not $ChoseState) {
        Say ""
        Say ("  Another copy of BuildBot (at " + $owner + ") uses " + $State + ". Share it, or give this copy its own folder?")
        for ($i = 0; $i -lt 3 -and $ans -notin @("s", "share", "o", "own"); $i++) {
            $ans = (Read-Host "  Type s to share it, or o to give this copy its own folder").Trim().ToLower()
        }
    }
    if ($ans -in @("o", "own")) {
        $NewState = Get-NewStateDir
        Say ("  This copy gets its own folder: " + $NewState + ". The other bot stays where it is.")
    } elseif ($ans -in @("s", "share")) {
        Say "  Sharing it."
    } else {
        Say ""
        Say ("  Careful: another copy of BuildBot (at " + $owner + ") already keeps its private files in " + $State + ".")
        Say "  Sharing it would mix up the two bots. To give this copy its own bot, run setup"
        Say "  again with:  install.cmd -NewBot"
        Say ""
    }
} elseif ($squatters.Count -gt 0 -and $owner -and ($owner -ne $RepoDir) -and -not $otherCopy) {
    Say ("  The bot in " + $State + " was set up from " + $owner + ", which is not there any more. This copy keeps it.")
}

if ($HasBot -and -not $Decided) {
    $persona  = Read-JsonFile $PersonaPath
    $manifest = Read-JsonFile $ManifestPath
    $OldName = ""
    if ($persona -and $persona.name) { $OldName = [string]$persona.name }
    elseif ($manifest -and $manifest.build -and $manifest.build.name) { $OldName = [string]$manifest.build.name }
    $OldName = ($OldName -replace '[^A-Za-z0-9 _.-]', '').Trim()
    $OldLabel = if ($OldName) { $OldName } else { "a bot" }
    $StartNew = $NewBot.IsPresent
    if ((-not $StartNew) -and $CanAsk) {
        Say ""
        Say ("  You already have " + $OldLabel + ". Keep it, or start a new bot?")
        $ans = ""
        for ($i = 0; $i -lt 3 -and $ans -notin @("k", "keep", "n", "new"); $i++) {
            $ans = (Read-Host "  Type k to keep it, or n to start a new bot").Trim().ToLower()
        }
        $StartNew = $ans -in @("n", "new")
    }
    if (-not $StartNew) {
        Say "  Keeping it."
        if (-not $CanAsk) { Say "  (To start a new bot instead, run setup again with -NewBot.)" }
    } else {
        $NewState = Get-NewStateDir
        Say ("  Starting a new bot. Its files go in " + $NewState + ".")
        if ($OldName) { Say ("  Your old bot (" + $OldName + ") stays where it is.") }
        else { Say "  Your old bot stays where it is." }
    }
}

# A new bot is set up exactly as if -StateDir had named its folder. Folders
# named in this window's environment belong to the old bot, so they are let go.
if ($NewState) {
    $OldDataDir = $DataDir
    $ignored = @("PUFFPAI_STATE_DIR", "PUFFPAI_DATA_DIR") | Where-Object { [Environment]::GetEnvironmentVariable($_) }
    if ($ignored) {
        Say ("  The new bot does not use " + ($ignored -join " or ") + " from this window.")
        foreach ($name in $ignored) { Remove-Item -LiteralPath ("Env:" + $name) }
    }
    $StateDir = $NewState
    Resolve-StateDirs
    # The reading helper is not the bot's: copy it over (the old bot keeps
    # its own) so it is not downloaded again.
    $oldHelper = Join-Path $OldDataDir "models\reading_helper"
    if ((-not $DryRun) -and (Test-Path -LiteralPath $oldHelper)) {
        New-Item -ItemType Directory -Path (Join-Path $DataDir "models") -Force | Out-Null
        Copy-Item -LiteralPath $oldHelper -Destination (Join-Path $DataDir "models") -Recurse -ErrorAction SilentlyContinue
    }
}

$EnvFile     = Join-Path $State "buildbot.env"
$BackendUrl  = "http://127.0.0.1:$Port"
$DashUrl     = "http://127.0.0.1:$DashboardPort/"
$RunSetup    = Join-Path $RepoDir "run-setup.ps1"
$RunBackend  = Join-Path $RepoDir "run-backend.ps1"
$RunDash     = Join-Path $RepoDir "run-dashboard.ps1"
$RunStart    = Join-Path $RepoDir "run-start.ps1"
$RunStop     = Join-Path $RepoDir "run-stop.ps1"
$SetupCmdPath = Join-Path $RepoDir "Set up BuildBot.cmd"
$ShimCmdPath = Join-Path $RepoDir "puffpai.cmd"
$DashDir     = Join-Path $RepoDir "dashboard"
$DashEnv     = Join-Path $DashDir ".env.local"
$LogDir      = Join-Path $State "logs"

if (-not $NewState) {
    Say ("  Your bot keeps its private files in: " + $State)
    if ($DataDir -ne $State) { Say ("  Its other files go in: " + $DataDir) }
}
Say ("  Your bot will use port " + $Port + ", and its web page port " + $DashboardPort + ".")

# A pointer whose env file is gone (its state folder was moved or deleted)
# makes every puffpai command print a warning, and under "Stop" Windows
# PowerShell 5.1 turns that warning into an error that ends setup. Fix it
# before anything below runs puffpai: point it at this install's env file when
# that is there, else remove it (it is written again at the end).
if (Test-Path -LiteralPath $Pointer) {
    $named = ""
    try { $named = [string]((Get-Content -LiteralPath $Pointer -Raw -ErrorAction Stop | ConvertFrom-Json).env_file) } catch { }
    if (-not $named -or -not (Test-Path -LiteralPath $named -PathType Leaf)) {
        $haveEnv = Test-Path -LiteralPath $EnvFile -PathType Leaf
        if ($DryRun) {
            Say ("  [dry-run] would " + $(if ($haveEnv) { "rewrite" } else { "remove" }) + " " + $Pointer + " (it names a settings file that is not there)")
        } elseif ($haveEnv) {
            Set-Content -LiteralPath $Pointer -Value (@{ env_file = $EnvFile } | ConvertTo-Json) -Encoding ASCII
        } else {
            Remove-Item -LiteralPath $Pointer -Force -ErrorAction SilentlyContinue
        }
    }
}

# A key is minted ONCE per state dir. Re-running the installer must not rotate
# it: the dashboard, the pairing registry and the vault were all set up against
# the existing one.
$Key = Get-EnvFileValue $EnvFile "PUFFPAI_API_KEY"
if ($Key.Length -ge 32) {
    Say "  Reusing this copy's private key (kept in buildbot.env, not printed)."
} else {
    if ($DryRun) { $Key = "<64 hex characters, minted when you run this for real>" }
    else { $Key = New-ApiKey }
    Say "  Made a private key so your bot and its web page know each other (saved in buildbot.env, not printed)."
}

# Order matters: create the file EMPTY, make it owner-only (and check that
# worked), and only then put the secret in. Written the other way round, the
# key sat in a file with the folder's inherited permissions until icacls ran.
function Write-Secret ($path, $body, $label) {
    if ($DryRun) {
        Say ("  [dry-run] would create " + $path + " empty, make it owner-only with icacls")
        Say  "  [dry-run]   (and stop the install if that fails), then write:"
        foreach ($line in ($body -split "`r?`n")) {
            if ($line -match '^\s*PUFFPAI_API_KEY\s*=') { Say "      PUFFPAI_API_KEY=<not printed>" }
            else { Say ("      " + $line) }
        }
        return
    }
    $dir = Split-Path -Parent $path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (-not (Test-Path $path)) { New-Item -ItemType File -Path $path -Force | Out-Null }
    Protect-File $path
    Set-Content -Path $path -Value $body -Encoding ASCII -NoNewline
}

# --- The reading helper (83 MB) ---------------------------------------------
# Lets the bot match questions worded differently from its books. Fetched once
# from one fixed address, checked against its pinned SHA-256 here and again by
# puffpai.knowledge.reading_helper before anything is unpacked. If it cannot
# be fetched, setup carries on: books and uploads still answer questions that
# use their own words.
$HelperUrl = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
$HelperSha = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
Say "  Getting the reading helper, 83 MB. It lets your bot match questions worded differently from its books."
if ($DryRun) {
    Say ("  [dry-run] would fetch " + $HelperUrl + " and check it before using it")
} else {
    $oldData = $env:PUFFPAI_DATA_DIR; $oldState = $env:PUFFPAI_STATE_DIR
    $env:PUFFPAI_DATA_DIR = $DataDir; $env:PUFFPAI_STATE_DIR = $State
    # A warning on stderr must not stop setup: under "Stop", Windows
    # PowerShell 5.1 throws on any redirected native stderr line.
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPy -m puffpai knowledge helper *> $null
        if ($LASTEXITCODE -eq 0) {
            Say "  It is already on this computer."
        } else {
            $tmp = Join-Path ([IO.Path]::GetTempPath()) ("puffpai-helper-" + [guid]::NewGuid().ToString("N") + ".tar.gz")
            try {
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 900 -Uri $HelperUrl -OutFile $tmp
                $got = (Get-FileHash -Algorithm SHA256 $tmp).Hash.ToLower()
                if ($got -ne $HelperSha) {
                    Say "  The download did not match its check number, so it was not used."
                } else {
                    & $VenvPy -m puffpai knowledge helper from-file $tmp
                }
            } catch {
                Say "  Could not get it now. Your bot still answers from its books; run setup again later to add it."
            } finally {
                Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            }
        }
    } finally {
        $env:PUFFPAI_DATA_DIR = $oldData; $env:PUFFPAI_STATE_DIR = $oldState
        $ErrorActionPreference = $prevPref
    }
}

# --- 4. Ollama -------------------------------------------------------------
Step 4 "Ollama (it runs the AI model on this computer)"

$OllamaOk = $false
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd -and -not (Pretends "ollama")) {
    # The file's own version, so nothing talks to a running Ollama here.
    $v = ""
    try { $v = (Get-Item $ollamaCmd.Source).VersionInfo.ProductVersion } catch { }
    if ($v) { Say ("  Found Ollama " + $v + ".") } else { Say "  Found Ollama." }
    $OllamaOk = $true
} else {
    $url = $env:OLLAMA_HOST
    if (-not $url) { $url = "http://127.0.0.1:11434" }
    if ($url -notmatch '^https?://') { $url = "http://" + $url }
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri ($url + "/api/tags") | Out-Null
        Say ("  Ollama is answering at " + $url + ".")
        $OllamaOk = $true
    } catch { }
}
if (-not $OllamaOk) {
    Say "  Ollama is not on this computer. Your bot needs it to think."
    if (Offer-Install "Ollama" "Ollama.Ollama" "https://ollama.com/download/windows" $YesInstallOllama.IsPresent) {
        $OllamaOk = $true
    }
}

if ($Pull -ne "") {
    Say ("  Downloading the AI model " + $Pull + " (you asked for it with -Pull).")
    Invoke-Step "ollama" @("pull", $Pull)
}

# --- 5. The web page (dashboard) --------------------------------------------
Step 5 "Your bot's web page"

function Test-NodeVersion {
    # Both floors: the runner's 22.4 ($NodeMinMajor.$NodeMinMinor) and the web
    # page's engines (^20.19 || >=22.12). Node 20 has no built-in WebSocket.
    $prevPref = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $raw = (& node --version 2>$null) } finally { $ErrorActionPreference = $prevPref }
    if ($raw -notmatch '^v(\d+)\.(\d+)') { return $false }
    $maj = [int]$Matches[1]; $min = [int]$Matches[2]
    if ($maj -lt $NodeMinMajor -or ($maj -eq $NodeMinMajor -and $min -lt $NodeMinMinor)) { return $false }
    return (($maj -eq 22 -and $min -ge $NodeWebPageMinor) -or ($maj -ge 23))
}
function Have ($name) { return ((Get-Command $name -ErrorAction SilentlyContinue) -and -not (Pretends $name)) }
function Test-NodeRunner {
    # Only the runner's floor (22.4): the part that lets your bot use a
    # browser app needs Node's built-in WebSocket, not the web page's build.
    $prevPref = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $raw = (& node --version 2>$null) } finally { $ErrorActionPreference = $prevPref }
    if ($raw -notmatch '^v(\d+)\.(\d+)') { return $false }
    $maj = [int]$Matches[1]; $min = [int]$Matches[2]
    return ($maj -gt $NodeMinMajor -or ($maj -eq $NodeMinMajor -and $min -ge $NodeMinMinor))
}
# The absolute path of the node program, for security.host_programs: outside
# parts are never started through a bare name the PATH could redirect. Written
# whether or not the web page is built (-NoDashboard too), because the app
# runner needs Node either way. A node that is missing or older than 22.4
# leaves no node_path.txt behind, so an old path is never used.
function Write-NodePath {
    $NodePathFile = Join-Path $RepoDir "node_path.txt"
    $nodeExe = $null
    if ((Have "node") -and (Test-NodeRunner)) {
        $nodeExe = (Get-Command node -ErrorAction SilentlyContinue).Source
    }
    if ($DryRun) {
        if ($nodeExe) { Say ("  [dry-run] would write the node program's place to " + $NodePathFile) }
        return
    }
    if ($nodeExe) {
        Set-Content -Path $NodePathFile -Value ([System.IO.Path]::GetFullPath($nodeExe)) -Encoding ASCII
    } elseif (Test-Path -LiteralPath $NodePathFile) {
        Remove-Item -LiteralPath $NodePathFile -Force -ErrorAction SilentlyContinue
    }
}

$DashboardBuilt = $false
if (-not $BuildDashboard) {
    Say "  Skipped (you asked for no web page). You can still set up and chat in a window."
    Write-NodePath
} else {
    $nodeOk = (Have "node") -and (Test-NodeVersion)
    if (-not $nodeOk) {
        if (Have "node") { Say ("  Node.js " + (node --version) + " is too old for BuildBot; it needs 22.12 or newer.") }
        else { Say "  Node.js is not on this computer. The web page needs it." }
        if (Offer-Install "Node.js" "OpenJS.NodeJS.LTS" "https://nodejs.org") {
            $nodeOk = (Have "node") -and (Test-NodeVersion)
        }
    } else {
        Say ("  Found Node.js " + (node --version) + ".")
    }
    Write-NodePath
    # No git needed: package-lock.json pins talkinghead to a GitHub commit, and
    # npm fetches that as a public https download (codeload.github.com), with
    # no git and no GitHub SSH key (checked with npm 11.9).

    # The two settings the proxy reads (app/api/pai/[...path]/route.ts). Written
    # before the build, and merged rather than overwritten: an existing
    # .env.local may hold MEDIA_SERVER_URL or a pairing setting that is none of
    # this script's business.
    $keep = @()
    if ((Test-Path $DashEnv) -and (-not $DryRun)) {
        foreach ($line in (Get-Content $DashEnv)) {
            if ($line -notmatch '^\s*(PUFFPAI_API_KEY|PUFFPAI_BACKEND_URL|NEXT_TELEMETRY_DISABLED)\s*=' -and
                $line -notmatch '^\s*#\s*Written by install\.') { $keep += $line }
        }
    }
    $dashBody = (@(
        "# Written by install.ps1 - the dashboard's half of this install's pair.",
        "# PUFFPAI_API_KEY here must equal the one run-backend.ps1 exports.",
        ("PUFFPAI_BACKEND_URL=" + $BackendUrl),
        ("PUFFPAI_API_KEY=" + $Key),
        "NEXT_TELEMETRY_DISABLED=1"
    ) + $keep) -join "`r`n"
    Write-Secret $DashEnv $dashBody "dashboard\.env.local"

    if ($nodeOk) {
        Say "  Building the web page. This takes a few minutes."
        $ok = Invoke-Logged "npm.cmd ci --no-audit --no-fund" $DashDir (Join-Path $LogDir "install-web-page.log")
        if ($ok) { $ok = Invoke-Logged "npm.cmd run build" $DashDir (Join-Path $LogDir "install-web-page-build.log") }
        if ($ok -and -not $DryRun) { $DashboardBuilt = $true; Say "  Done." }
        if (-not $ok) { Say "  The web page was not built. You can still set up and chat in a window." }
    } else {
        Say "  The web page was not built. You can still set up and chat in a window,"
        Say "  or install what is missing and run this setup again."
    }
}

# This install's environment. Written after the web-page step so that
# PUFFPAI_WITH_DASHBOARD records what actually happened: 1 only when the web
# page was built (the wizard's closing screen reads it; see
# cli.installed_launchers). Under -DryRun it records what was asked for.
$withDash = if ($DryRun) { $BuildDashboard } else { $DashboardBuilt }
$envBody = @(
    "# PuffPAI BuildBot - the environment THIS install runs with.",
    "# Written by install.ps1. Holds a secret: keep it owner-readable.",
    "# Delete this file to mint a new key; re-running the installer reuses it.",
    ("PUFFPAI_CHECKOUT=" + $RepoDir),
    ("PUFFPAI_API_KEY=" + $Key),
    ("PUFFPAI_DATA_DIR=" + $DataDir),
    ("PUFFPAI_STATE_DIR=" + $State),
    ("PUFFPAI_PORT=" + $Port),
    ("PUFFPAI_DASHBOARD_PORT=" + $DashboardPort),
    ("PUFFPAI_WITH_DASHBOARD=" + $(if ($withDash) { "1" } else { "0" })),
    ("PUFFPAI_BACKEND_URL=" + $BackendUrl),
    "NEXT_TELEMETRY_DISABLED=1"
) -join "`r`n"
Write-Secret $EnvFile $envBody "buildbot.env"

# --- 6. Launchers -----------------------------------------------------------
#
# What an owner double-clicks is always a .cmd file: a stock Windows client
# has an execution policy of Restricted, which blocks every .ps1 - including
# the ones this script writes. Each .cmd either does its job in plain batch
# (Set up, puffpai) or hands over to a .ps1 helper with -ExecutionPolicy Bypass
# for that one process (Start, Stop). The run-*.ps1 helpers stay because the
# wizard's closing screen names run-backend.ps1 / run-dashboard.ps1.
Step 6 "Shortcuts you can double-click"

# Every generated file carries this line, so a re-run can tell its own old
# launchers from anything the owner wrote.
$Marker = "Generated by install.ps1"

$loadEnvPs = @'
$EnvFile = "__ENVFILE__"
if (-not (Test-Path $EnvFile)) {
    Write-Host "Your bot's settings file is missing: $EnvFile"
    Write-Host "Double-click install.cmd to set BuildBot up again."
    exit 1
}
foreach ($line in (Get-Content $EnvFile)) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), "Process")
    }
}
'@

$setupTemplate = @'
# Generated by install.ps1. Re-run the installer to regenerate.
#
# Runs the wizard against THIS install's state dir. A bare "puffpai setup" also
# works - the installer leaves .puffpai-install.json beside this file and the
# CLI reads it - but this launcher is the one that cannot be got wrong.
$ErrorActionPreference = "Stop"
__LOADENV__
& "__VENVEXE__" setup @args
'@

$backendTemplate = @'
# Generated by install.ps1. Re-run the installer to regenerate.
#
# Starts THIS checkout's backend with THIS install's key, state dir and port.
# Use it instead of a bare "puffpai serve": the key it exports wins over any
# stored one, which is what keeps the dashboard from getting a 401 after
# another install left an entry in Windows Credential Manager.
$ErrorActionPreference = "Stop"
__LOADENV__
$Port = $env:PUFFPAI_PORT
if (-not $Port) { $Port = "__PORT__" }
# This window's own number, so Remove BuildBot can stop what it started.
try { @{ pid = $PID } | ConvertTo-Json | Set-Content -Path (Join-Path $env:PUFFPAI_STATE_DIR "running.bot.json") -Encoding ASCII } catch { }
Write-Host "Your bot is starting on http://127.0.0.1:$Port"
& "__VENVEXE__" serve --host 127.0.0.1 --port $Port
'@

$dashTemplate = @'
# Generated by install.ps1. Re-run the installer to regenerate.
#
# Starts the dashboard on this install's port, pointed at this install's
# backend, holding this install's key. package.json's "start" is pinned to
# 3003; this is the way to pick a port.
$ErrorActionPreference = "Stop"
__LOADENV__
$DashPort = $env:PUFFPAI_DASHBOARD_PORT
if (-not $DashPort) { $DashPort = "__DASHPORT__" }
# No usage reports to the web page's build tool maker (zero telemetry).
$env:NEXT_TELEMETRY_DISABLED = "1"
# This window's own number, so Remove BuildBot can stop what it started.
try { @{ pid = $PID } | ConvertTo-Json | Set-Content -Path (Join-Path $env:PUFFPAI_STATE_DIR "running.page.json") -Encoding ASCII } catch { }
Set-Location "__DASHDIR__"
# Same staleness guard "npm run start" gets from its prestart hook.
& node scripts\ensure-fresh-build.js
Write-Host "Your bot's web page is starting on http://127.0.0.1:$DashPort"
& npx.cmd next start -H 127.0.0.1 -p $DashPort
'@

# Start: the backend and the dashboard together, in hidden windows of their
# own (so closing this window does not stop them), then wait until both answer,
# print the pairing code, and open the browser. PIDs go to <state>\running.json
# so Stop kills exactly what Start started.
$startTemplate = @'
# Generated by install.ps1. Re-run the installer to regenerate.
#
# Called by "Start <name>.cmd" with -ExecutionPolicy Bypass. Starts the bot and
# its web page, waits until both answer, and opens the web page.
# PUFFPAI_NO_BROWSER=1 prints the address instead of opening it.
param([switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
__LOADENV__
$Repo     = "__REPO__"
$DashDir  = Join-Path $Repo "dashboard"
$Py       = Join-Path $Repo "venv\Scripts\python.exe"
$Port     = $env:PUFFPAI_PORT;           if (-not $Port)     { $Port = "__PORT__" }
$DashPort = $env:PUFFPAI_DASHBOARD_PORT; if (-not $DashPort) { $DashPort = "__DASHPORT__" }
$Logs     = Join-Path $env:PUFFPAI_STATE_DIR "logs"
$RunFile  = Join-Path $env:PUFFPAI_STATE_DIR "running.json"
$Url      = "http://127.0.0.1:$DashPort/"
# No usage reports to the web page's build tool maker (zero telemetry).
$env:NEXT_TELEMETRY_DISABLED = "1"
if (-not (Test-Path $Logs)) { New-Item -ItemType Directory -Path $Logs -Force | Out-Null }

# A plain .NET request, not Invoke-WebRequest: Windows PowerShell 5.1 copies
# every Invoke-WebRequest timeout into the setup log even when it is caught,
# and this asks every two seconds while the bot starts.
function Answers ($u) {
    $resp = $null
    try {
        $req = [Net.WebRequest]::Create($u); $req.Timeout = 2000
        $resp = $req.GetResponse()
        return ([int]$resp.StatusCode -eq 200)
    } catch { return $false }
    finally { if ($resp) { $resp.Close() } }
}
function Alive ($procId) {
    if (-not $procId) { return $false }
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    return [bool]$p
}
function Start-Hidden ($cmdLine, $workDir, $log) {
    $inner = $cmdLine + ' > "' + $log + '" 2>&1'
    return Start-Process -FilePath "cmd.exe" -ArgumentList ('/d /s /c "' + $inner + '"') `
        -WorkingDirectory $workDir -WindowStyle Hidden -PassThru
}
function Wait-Up ($label, $u, $proc, $seconds, $log) {
    Write-Host -NoNewline ("  Starting " + $label + "...")
    $until = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $until) {
        if (Answers $u) { Write-Host " ready."; return $true }
        if ($proc -and $proc.HasExited) { break }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 2
    }
    Write-Host " it did not start."
    Write-Host ("  The last lines of " + $log + ":")
    Get-Content $log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ("    " + $_) }
    return $false
}

# Checked before anything starts, so nothing is left running when the web
# page cannot open.
$next = Join-Path $DashDir "node_modules\next\dist\bin\next"
$built = (Test-Path (Join-Path $DashDir ".next\BUILD_ID")) -and (Test-Path $next) -and (Get-Command node -ErrorAction SilentlyContinue)
if (-not $built) {
    Write-Host "  Your bot's web page is not built on this computer, so it cannot open."
    Write-Host "  You can set up your bot in a window instead: double-click ""Set up BuildBot.cmd""."
    Write-Host "  To get the web page, double-click install.cmd again."
    exit 1
}

$prev = $null
if (Test-Path $RunFile) { try { $prev = Get-Content $RunFile -Raw | ConvertFrom-Json } catch { } }
$backendPid = $null; $dashPid = $null

Write-Host ""
# The bot itself.
if ($prev -and (Alive $prev.backend_pid) -and (Answers "http://127.0.0.1:$Port/health")) {
    Write-Host "  Your bot is already running."
    $backendPid = $prev.backend_pid
} elseif (Answers "http://127.0.0.1:$Port/health") {
    Write-Host "  Something else on this computer is already using port $Port."
    Write-Host "  Stop that program, or run install.cmd again with -Port and another number."
    exit 1
} else {
    $log = Join-Path $Logs "bot.log"
    $p = Start-Hidden ('"' + $Py + '" -m puffpai serve --host 127.0.0.1 --port ' + $Port) $Repo $log
    $backendPid = $p.Id
    @{ backend_pid = $backendPid; dashboard_pid = $null } | ConvertTo-Json | Set-Content -Path $RunFile -Encoding ASCII
    if (-not (Wait-Up "your bot" "http://127.0.0.1:$Port/health" $p 180 $log)) { exit 1 }
}

# The web page.
if ($prev -and (Alive $prev.dashboard_pid) -and (Answers ($Url + "api/health"))) {
    Write-Host "  Your bot's web page is already running."
    $dashPid = $prev.dashboard_pid
} elseif (Answers ($Url + "api/health")) {
    Write-Host "  Something else on this computer is already using port $DashPort."
    Write-Host "  Stop that program, or run install.cmd again with -DashboardPort and another number."
    exit 1
} else {
    Write-Host "  Checking the web page is up to date (if it rebuilds, that takes a few minutes)."
    Push-Location $DashDir
    # "Continue": a build warning on stderr must not stop the start.
    $prevPref = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & node scripts\ensure-fresh-build.js *> (Join-Path $Logs "web-page-check.log") } finally { Pop-Location; $ErrorActionPreference = $prevPref }
    $log = Join-Path $Logs "web-page.log"
    $p = Start-Hidden ('node "' + $next + '" start -H 127.0.0.1 -p ' + $DashPort) $DashDir $log
    $dashPid = $p.Id
    @{ backend_pid = $backendPid; dashboard_pid = $dashPid } | ConvertTo-Json | Set-Content -Path $RunFile -Encoding ASCII
    if (-not (Wait-Up "your bot's web page" ($Url + "api/health") $p 120 $log)) { exit 1 }
}

# The pairing code: the web page only opens for devices that have typed it once.
$code = ""
# "Continue" for the two puffpai calls below: a warning on stderr would
# otherwise throw, and the code and the bot's name would silently go missing.
$ErrorActionPreference = "Continue"
try {
    # THIS bot's code: pairing_code finds its state folder from
    # PUFFPAI_STATE_DIR (loaded above), the same way the web page does.
    Push-Location $Repo
    try { $code = (& $Py -c "from puffpai import pairing_code as p; i = p.ensure_code(); print(p.format_code(i['code']) if i else '')" 2>$null | Select-Object -Last 1) }
    finally { Pop-Location }
} catch { }

# A launcher named after the bot, once the wizard has named it.
$stopFile = "Stop BuildBot.cmd"
try {
    $name = (& $Py -c "from puffpai import bot_identity as b; n = (b.bot_name(fallback='') or '').strip(); print(n if n.lower() not in b._shipped_names() else '')" 2>$null | Select-Object -Last 1)
    $name = (($name -replace '[^A-Za-z0-9 _.-]', '').Trim())
    if ($name.Length -gt 40) { $name = $name.Substring(0, 40).Trim() }
    if ($name -and $name -ne "BuildBot") {
        $named = Join-Path $Repo ("Start " + $name + ".cmd")
        if (-not (Test-Path $named)) {
            Copy-Item (Join-Path $Repo "Start BuildBot.cmd") $named -ErrorAction Stop
            Copy-Item (Join-Path $Repo "Stop BuildBot.cmd") (Join-Path $Repo ("Stop " + $name + ".cmd")) -ErrorAction Stop
            Write-Host ("  From now on you can use ""Start " + $name + ".cmd"" and ""Stop " + $name + ".cmd"".")
        }
        $stopFile = "Stop " + $name + ".cmd"
    }
} catch { }
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  Your bot is running on this computer."
Write-Host ("  Its web page: " + $Url)
if ($code) {
    Write-Host ("  The first time, the page asks for a pairing code. Type: " + $code)
    Write-Host "  (The code works for one hour. After that, start again to get a new one.)"
}
if ($NoBrowser -or $env:PUFFPAI_NO_BROWSER -eq "1") {
    Write-Host ("  Open this address in your web browser: " + $Url)
} else {
    Write-Host "  Opening your web browser now."
    # Through explorer.exe, so the browser is not started from this window and
    # cannot print into it (it left a stray "System error." line behind).
    try { Start-Process -FilePath "explorer.exe" -ArgumentList $Url }
    catch { Write-Host ("  Could not open it. Open this address in your web browser: " + $Url) }
}
Write-Host ("  It keeps running after you close this window. To stop it, double-click """ + $stopFile + """.")
exit 0
'@

$stopTemplate = @'
# Generated by install.ps1. Re-run the installer to regenerate.
#
# Called by "Stop <name>.cmd". Stops exactly the processes "Start" recorded in
# <state>\running.json, by PID, with their child processes - and only if each
# PID still belongs to a process that started before that record was written
# (so a reused PID is never touched).
$ErrorActionPreference = "Stop"
__LOADENV__
$RunFile = Join-Path $env:PUFFPAI_STATE_DIR "running.json"
if (-not (Test-Path $RunFile)) { Write-Host "  Your bot was not running."; exit 0 }
$rec = $null
try { $rec = Get-Content $RunFile -Raw | ConvertFrom-Json } catch { }
$written = (Get-Item $RunFile).LastWriteTime.AddSeconds(5)
$any = $false
foreach ($pair in @(@("web page", $rec.dashboard_pid), @("bot", $rec.backend_pid))) {
    $label = $pair[0]; $procId = $pair[1]
    if (-not $procId) { continue }
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p -and $p.StartTime -le $written) {
        $prevPref = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try { & taskkill.exe /PID $procId /T /F 2>&1 | Out-Null } finally { $ErrorActionPreference = $prevPref }
        Write-Host ("  Stopped your " + $label + ".")
        $any = $true
    }
}
Remove-Item $RunFile -Force -ErrorAction SilentlyContinue
if (-not $any) { Write-Host "  Your bot was not running." }
exit 0
'@

# Remove BuildBot. "Remove BuildBot.cmd" copies the
# generated remove-buildbot.ps1 to %TEMP% and runs it from there, so no file
# inside the BuildBot folder is in use while the folder is deleted. The same
# plan as "puffpai remove" (puffpai.remove_cli), which the script asks for the
# facts only Python knows (the locked box, the saved entries, the vault and
# apps folders) and which removes the box (through its guard and claim count)
# and the saved entries. Paths are written into the script as single-quoted
# literals, so no character in a folder name can become code.
$removeTemplate = @'
# Generated by install.ps1. Re-run the installer to regenerate.
#
# Remove BuildBot. Stops the bot and its web page, says what will be deleted,
# asks once, then deletes. It never enters a link or junction (links are
# removed as links, their targets are left alone), never deletes outside this
# install's own folders, touches the locked box only through BuildBot's own
# guard, and stops only programs whose file is inside the BuildBot folder or
# whose number Start wrote down; never by what a program was started with.
param([string]$InstallRoot = "", [switch]$Yes, [switch]$KeepVault)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo    = '__RM_REPO__'
$State   = '__RM_STATE__'
$Data    = '__RM_DATA__'
$EnvFile = '__RM_ENVFILE__'
$VenvPy  = Join-Path $Repo "venv\Scripts\python.exe"
$Sys32   = Join-Path $env:SystemRoot "System32"
$TaskKill = Join-Path $Sys32 "taskkill.exe"
$TempDir = [System.IO.Path]::GetTempPath()
Set-Location -LiteralPath $TempDir

function Say ($m) { Write-Host $m }
function Full ($p) { return ([System.IO.Path]::GetFullPath([string]$p)).TrimEnd('\') }
function Is-Under ($child, $parent) {
    $c = (Full $child).ToLowerInvariant(); $p = (Full $parent).ToLowerInvariant()
    return (($c -eq $p) -or $c.StartsWith($p + "\"))
}
function Finish ($code) {
    $me = $PSCommandPath
    if ($me -and ((Split-Path -Leaf $me) -like "puffpai-remove-*.ps1") -and (Is-Under $me $TempDir)) {
        Remove-Item -LiteralPath $me -Force -ErrorAction SilentlyContinue
    }
    if ($env:PUFFPAI_NO_PAUSE -ne "1") {
        Write-Host ""
        Write-Host "Press Enter to close this window."
        [void][Console]::ReadLine()
    }
    exit $code
}
function Ask () { $a = [Console]::ReadLine(); if ($null -eq $a) { return "" }; return ([string]$a).Trim().ToLowerInvariant() }

if ($InstallRoot -and ((Full $InstallRoot) -ne (Full $Repo))) {
    Say "This removal file belongs to a different BuildBot folder, so nothing was removed."
    Finish 2
}

# The profile, its main folders and the system places: never deleted, and
# never inside a folder that is deleted.
$Protected = @()
foreach ($f in @("UserProfile", "Desktop", "MyDocuments", "MyPictures", "MyMusic", "MyVideos",
                 "ApplicationData", "LocalApplicationData", "Windows", "ProgramFiles",
                 "ProgramFilesX86", "CommonApplicationData")) {
    $p = [Environment]::GetFolderPath($f)
    if ($p) { $Protected += (Full $p) }
}
if ($env:USERPROFILE) {
    $Protected += (Full (Split-Path -Parent $env:USERPROFILE))
    foreach ($n in @("Pictures", "Documents", "Music", "Videos", "Desktop", "Downloads")) {
        $Protected += (Full (Join-Path $env:USERPROFILE $n))
    }
}
$Protected += (Full $TempDir)

function Root-Problem ($root, $kind) {
    $full = Full $root
    $item = Get-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { return "a link to another place" }
    if (([System.IO.Path]::GetPathRoot($full)).TrimEnd('\') -eq $full) { return "a whole drive" }
    foreach ($p in $Protected) { if (Is-Under $p $full) { return "one of your own main folders, or holds one" } }
    if ($kind -eq "folder") {
        $ok = $false
        foreach ($s in @(".puffpai-install.json", "install.ps1", "install.sh", "src\puffpai\__init__.py")) {
            if (Test-Path -LiteralPath (Join-Path $full $s)) { $ok = $true }
        }
        if (-not $ok) { return "not a BuildBot folder" }
    }
    if ($kind -eq "state") {
        $ok = $false
        foreach ($s in @("buildbot.env", "build_id.json", "config\build_manifest.json", "api_key.txt",
                         "addition_checks.jsonl", "running.json", "pairing")) {
            if (Test-Path -LiteralPath (Join-Path $full $s)) { $ok = $true }
        }
        if (-not $ok) { return "not a BuildBot folder" }
    }
    return ""
}
function Env-Value ($file, $key) {
    if (-not (Test-Path -LiteralPath $file)) { return "" }
    foreach ($line in (Get-Content -LiteralPath $file -Encoding UTF8)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -and $Matches[1] -eq $key) { return $Matches[2].Trim() }
    }
    return ""
}
# A state folder whose own env file names another BuildBot folder holds that
# install's settings: it stays.
$StateStays = $false
$StateOwner = Env-Value (Join-Path $State "buildbot.env") "PUFFPAI_CHECKOUT"
if ($StateOwner -and ((Full $StateOwner) -ne (Full $Repo))) { $StateStays = $true }

# This install's settings, so BuildBot's own Python reads the same folders.
if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in (Get-Content -LiteralPath $EnvFile)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), "Process")
        }
    }
}

# What only Python knows. Used only when it names this same install.
$Facts = $null
if (Test-Path -LiteralPath $VenvPy) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        # Its input is empty, never this window's: a program it starts
        # (some read whatever input they inherit) must never swallow the
        # answers typed below.
        $raw = @() | & $VenvPy -m puffpai.remove_cli --facts 2>$null
        $f = ($raw | Select-Object -Last 1) | ConvertFrom-Json
        if ($f -and ((Full $f.install_root) -eq (Full $Repo)) -and ((Full $f.state_dir) -eq (Full $State))) { $Facts = $f }
    } catch { $Facts = $null } finally { $ErrorActionPreference = $prev }
}

$BoxRoot = $env:PUFFPAI_BOX_ROOT
if (-not $BoxRoot) { $BoxRoot = Join-Path $env:LOCALAPPDATA "PuffPAI\box" }
$Keeps = @((Full $BoxRoot))
$AppsRoot = ""; $AppsDelete = $false; $AppsOwn = @(); $AppsOthers = $false; $BoxProjects = 0
$VaultDir = Join-Path $Data "vault_data"
if ($Facts) {
    foreach ($Refused in @($Facts.refused)) {
        if ($Refused -and $Refused.path) {
            Say ("BuildBot will not delete " + $Refused.path + ", because it is " + $Refused.why + ". Nothing was deleted.")
            Finish 2
        }
    }
    foreach ($k in @($Facts.keeps)) { if ($k) { $Keeps += (Full $k) } }
    if ($Facts.state_stays) { $StateStays = $true }
    $AppsRoot = [string]$Facts.apps_root
    $AppsDelete = [bool]$Facts.apps_delete
    # Only this install's own app folders (named by ids it issued), when the
    # rest of the apps folder stays. Each must be directly inside it.
    foreach ($a in @($Facts.apps_own)) {
        if ($a -and $AppsRoot -and ((Full (Split-Path -Parent ([string]$a))) -eq (Full $AppsRoot))) { $AppsOwn += (Full $a) }
    }
    $AppsOthers = [bool]$Facts.apps_others
    $BoxProjects = [int]$Facts.box_projects
    $VaultDir = [string]$Facts.vault_dir
} else {
    # Without BuildBot's own Python nobody can say whether another BuildBot
    # uses the apps folder, so it stays (even when it sits in the state folder).
    $Keeps += (Full (Join-Path $env:LOCALAPPDATA "PuffPAI\apps"))
    if ($env:PUFFPAI_APPS_ROOT) { $Keeps += (Full $env:PUFFPAI_APPS_ROOT) }
}
# $Later: what a delete step leaves for a later step (empty outside them).
# $NotKept: the apps folder while one of this install's own app folders in it
# is looked at or deleted (the rest of the apps folder stays kept).
$Later = @()
$NotKept = ""
function Keep-List { return @(@($Keeps) + @($script:Later) | Where-Object { $_ -and -not ($script:NotKept -and ((Full $_) -eq $script:NotKept)) }) }
function Is-Kept ($p) { foreach ($k in (Keep-List)) { if (Is-Under $p $k) { return $true } }; return $false }
function Holds-Kept ($p) { foreach ($k in (Keep-List)) { if (Is-Under $k $p) { return $true } }; return $false }
function Not-Kept-For ($kind) { if ($kind -eq "app" -and $AppsRoot) { return (Full $AppsRoot) }; return "" }

function Size-Of ($dir) {
    $total = [int64]0
    $stack = New-Object System.Collections.Stack
    $stack.Push($dir)
    while ($stack.Count -gt 0) {
        $d = $stack.Pop()
        foreach ($i in @(Get-ChildItem -LiteralPath $d -Force -Attributes !ReparsePoint -ErrorAction SilentlyContinue)) {
            if ($i.PSIsContainer) { if (-not (Is-Kept $i.FullName)) { $stack.Push($i.FullName) } }
            else { $total += [int64]$i.Length }
        }
    }
    return $total
}
function Size-Words ($n) {
    if ($n -ge 1GB) { return [string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "{0:0.0} GB", ($n / 1GB)) }
    if ($n -ge 1MB) { return ([string][math]::Floor($n / 1MB) + " MB") }
    return "less than 1 MB"
}

$Wanted = New-Object System.Collections.ArrayList
[void]$Wanted.Add(@("folder", $Repo))
if ($StateStays) { $Keeps += (Full $State) } else { [void]$Wanted.Add(@("state", $State)) }
if ($AppsDelete -and $AppsRoot) { [void]$Wanted.Add(@("apps", $AppsRoot)) }
elseif ($AppsOwn.Count -gt 0) {
    $why = Root-Problem $AppsRoot "apps"
    if ($why) {
        Say ("BuildBot will not delete " + (Full $AppsRoot) + ", because it is " + $why + ". Nothing was deleted.")
        Finish 2
    }
    foreach ($a in $AppsOwn) { [void]$Wanted.Add(@("app", $a)) }
}
$Roots = New-Object System.Collections.ArrayList
foreach ($w in $Wanted) {
    $kind = $w[0]; $path = Full $w[1]
    if (-not (Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue)) { continue }
    $why = Root-Problem $path $kind
    if ($why) {
        Say ("BuildBot will not delete " + $path + ", because it is " + $why + ". Nothing was deleted.")
        Finish 2
    }
    $script:NotKept = Not-Kept-For $kind
    [void]$Roots.Add(@($kind, $path, (Size-Of $path)))
    $script:NotKept = ""
}
function Is-Covered ($p) {
    foreach ($r in $Roots) {
        $script:NotKept = Not-Kept-For $r[0]
        $hit = ((Is-Under $p $r[1]) -and -not (Is-Kept $p))
        $script:NotKept = ""
        if ($hit) { return $true }
    }
    return $false
}

$BoxLine = ($Facts -and $Facts.box)
$SavedCount = 0
if ($Facts) { $SavedCount = [int]$Facts.keyring }
$VaultGoes = ((Test-Path -LiteralPath $VaultDir) -and (Is-Covered $VaultDir))
if ($Roots.Count -eq 0 -and -not $BoxLine -and $SavedCount -eq 0 -and $BoxProjects -eq 0) {
    Say "There is nothing of BuildBot left to remove."
    Finish 0
}

Say "Remove BuildBot"
Say ""
Say "This stops your bot and its web page, then deletes:"
$OwnCount = 0; $OwnBytes = [int64]0
foreach ($r in $Roots) {
    $size = Size-Words $r[2]
    if ($r[0] -eq "folder") { Say ("  the BuildBot folder: " + $r[1] + " (" + $size + ")") }
    elseif ($r[0] -eq "state") { Say ("  your bot's settings, books and chats: " + $r[1] + " (" + $size + ")") }
    elseif ($r[0] -eq "app") { $OwnCount++; $OwnBytes += [int64]$r[2] }
    else { Say ("  the apps BuildBot added: " + $r[1] + " (" + $size + ")") }
}
if ($OwnCount -eq 1) { Say ("  the 1 app this BuildBot added, in " + (Full $AppsRoot) + " (" + (Size-Words $OwnBytes) + ")") }
elseif ($OwnCount -gt 1) { Say ("  the " + $OwnCount + " apps this BuildBot added, in " + (Full $AppsRoot) + " (" + (Size-Words $OwnBytes) + ")") }
if ($BoxLine) { Say ("  the locked box BuildBot set up: " + $Facts.box.distro + " (" + (Size-Words ([int64]$Facts.box.bytes)) + ")") }
elseif ($BoxProjects -eq 1) { Say "  the 1 app or tool this BuildBot put in the locked box" }
elseif ($BoxProjects -gt 1) { Say ("  the " + $BoxProjects + " apps and tools this BuildBot put in the locked box") }
if ($SavedCount -eq 1) { Say "  1 saved entry BuildBot kept in Windows Credential Manager" }
elseif ($SavedCount -gt 1) { Say ("  " + $SavedCount + " saved entries BuildBot kept in Windows Credential Manager") }
$KeepCopy = [bool]$KeepVault
if ($VaultGoes) {
    Say "Your vault (your private files and keys) is deleted too, and that cannot be undone."
    Say "  Type K to keep a copy of the vault in Documents first, or press Enter to go on."
    if (-not $Yes) { $KeepCopy = ((Ask) -eq "k") }
}
if (-not $Facts) { Say "The locked box and the saved entries could not be checked, so they were left as they are." }
if ($StateStays) {
    Say ("Your bot's settings stay in " + $State + ", because they belong to another BuildBot folder.")
}
if ((Test-Path -LiteralPath $Data) -and -not (Is-Covered $Data)) {
    Say ("BuildBot's other files stay in " + $Data + ", because that folder is outside BuildBot's own folders.")
}
if ($Facts -and -not $AppsDelete -and $AppsOthers -and $AppsRoot -and (Test-Path -LiteralPath $AppsRoot)) {
    if ($OwnCount -gt 0) { Say ("Other apps in " + $AppsRoot + " stay, because another BuildBot on this computer may use them.") }
    else { Say ("The apps BuildBot added stay in " + $AppsRoot + ", because another BuildBot on this computer may use them.") }
}
Say "Your own files in Pictures, Documents and other folders are not touched."
Say ""
if (-not $Yes) {
    Say "Remove all of this? Type Y and press Enter. Anything else keeps it."
    if ((Ask) -notin @("y", "yes")) {
        Say "Nothing was removed."
        Finish 1
    }
}

# 1. Stop: what a start wrote down (and only if it started before that record
# was written, so a reused number is never touched), every program whose file
# is inside the BuildBot folder and, through BuildBot's own Python, every
# program running a file from a folder about to be deleted (the web page's
# node.exe lives outside the BuildBot folder). Never by what a program was
# started with, and never this window or what started it.
Write-Host -NoNewline "Stopping your bot ..."
$RunRecords = @("running.json", "running.bot.json", "running.page.json")
$StillNames = @()
function Stop-Ours {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $all = @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue)
        $parents = @{}
        foreach ($p in $all) { $parents[[int]$p.ProcessId] = [int]$p.ParentProcessId }
        $mine = @{}
        $cur = [int]$PID
        while ($cur -gt 0 -and -not $mine.ContainsKey($cur)) {
            $mine[$cur] = $true
            if ($parents.ContainsKey($cur)) { $cur = $parents[$cur] } else { $cur = 0 }
        }
        $targets = New-Object System.Collections.ArrayList
        foreach ($name in $RunRecords) {
            $RunFile = Join-Path $State $name
            if (-not (Test-Path -LiteralPath $RunFile)) { continue }
            $rec = $null
            try { $rec = Get-Content -LiteralPath $RunFile -Raw | ConvertFrom-Json } catch { }
            $written = (Get-Item -LiteralPath $RunFile).LastWriteTime.AddSeconds(5)
            foreach ($procId in @($rec.dashboard_pid, $rec.backend_pid, $rec.pid)) {
                if (-not $procId) { continue }
                $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
                if ($p -and $p.StartTime -and $p.StartTime -le $written -and -not $mine.ContainsKey([int]$procId) -and -not $targets.Contains([int]$procId)) {
                    [void]$targets.Add([int]$procId)
                }
            }
        }
        foreach ($p in $all) {
            $exe = [string]$p.ExecutablePath
            $id = [int]$p.ProcessId
            if (-not $exe -or $id -le 4 -or $mine.ContainsKey($id) -or $targets.Contains($id)) { continue }
            if (Is-Under $exe $Repo) { [void]$targets.Add($id) }
        }
        foreach ($id in $targets) { @() | & $TaskKill /PID $id /T /F 2>&1 | Out-Null }
        $until = (Get-Date).AddSeconds(10)
        while ($targets.Count -gt 0 -and (Get-Date) -lt $until) {
            $left = @($targets | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
            if ($left.Count -eq 0) { break }
            Start-Sleep -Milliseconds 250
        }
        if ($Facts -and (Test-Path -LiteralPath $VenvPy)) {
            $raw = @() | & $VenvPy -m puffpai.remove_cli --only stop --yes 2>$null
            try { $got = ($raw | Select-Object -Last 1) | ConvertFrom-Json; $script:StillNames = @($got.still) } catch { }
        }
    } finally { $ErrorActionPreference = $prev }
}
Stop-Ours
foreach ($name in $RunRecords) { Remove-Item -LiteralPath (Join-Path $State $name) -Force -ErrorAction SilentlyContinue }
Write-Host " done"

# Nothing is deleted while Windows still holds any of it: a bot left without
# its settings and vault while its web page still runs is the worst end. A
# folder cannot be renamed while a file in it is open or a program works
# inside it, so each one is renamed and straight back.
function Probe-Free ($p) {
    $item = Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
    if (-not $item) { return "" }
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return "" }
    if (Is-Kept $p) { return "" }
    if (Holds-Kept $p) {
        foreach ($c in @(Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue)) {
            $r = Probe-Free $c.FullName
            if ($r) { return $r }
        }
        return ""
    }
    $tmp = $p + ".puffpai-check-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    try {
        if ($item.PSIsContainer) { [System.IO.Directory]::Move($p, $tmp) } else { [System.IO.File]::Move($p, $tmp) }
    } catch { return $p }
    for ($i = 0; $i -lt 40; $i++) {
        try {
            if ($item.PSIsContainer) { [System.IO.Directory]::Move($tmp, $p) } else { [System.IO.File]::Move($tmp, $p) }
            return ""
        } catch { Start-Sleep -Milliseconds 250 }
    }
    throw ("PUFFPAI-MOVED|" + $p + "|" + $tmp)
}
function Busy-Root {
    foreach ($r in $Roots) {
        $script:NotKept = Not-Kept-For $r[0]
        try { $b = Probe-Free $r[1] } finally { $script:NotKept = "" }
        if ($b) { return $b }
    }
    return ""
}
$Busy = ""
try {
    $Busy = Busy-Root
    $until = (Get-Date).AddSeconds(30)
    while ($Busy -and (Get-Date) -lt $until) {
        Start-Sleep -Seconds 1
        Stop-Ours
        $Busy = Busy-Root
    }
} catch {
    $m = [string]$_.Exception.Message
    if (-not $m.StartsWith("PUFFPAI-MOVED|")) { throw }
    $parts = $m.Split("|")
    Say ("BuildBot could not put back the name of " + $parts[1] + " after checking it. It is now called " + $parts[2] + ". Nothing was deleted. Rename it back, then run Remove BuildBot again.")
    Finish 1
}
if ($Busy) {
    Say ("Windows is still using a file in " + $Busy + ", so nothing was deleted. Close the program that uses it, or restart the computer, then run Remove BuildBot again.")
    if (@($StillNames).Count -gt 0) { Say ("Still running from it: " + (@($StillNames) -join ", ") + ".") }
    Finish 1
}

# 2. The vault copy, when asked for. Links inside it are not followed.
function Copy-Tree ($src, $dst) {
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    foreach ($i in @(Get-ChildItem -LiteralPath $src -Force -Attributes !ReparsePoint)) {
        $to = Join-Path $dst $i.Name
        if ($i.PSIsContainer) { Copy-Tree $i.FullName $to }
        else { Copy-Item -LiteralPath $i.FullName -Destination $to -Force }
    }
}
if ($KeepCopy -and $VaultGoes) {
    try {
        $docs = [Environment]::GetFolderPath("MyDocuments")
        $base = Join-Path $docs ("BuildBot vault copy " + (Get-Date -Format "yyyy-MM-dd"))
        $dest = $base; $n = 2
        while (Test-Path -LiteralPath $dest) { $dest = $base + " (" + $n + ")"; $n++ }
        $leaf = Split-Path -Leaf $VaultDir
        Copy-Tree $VaultDir (Join-Path $dest $leaf)
        if (Test-Path -LiteralPath ($VaultDir + ".inbox")) { Copy-Tree ($VaultDir + ".inbox") (Join-Path $dest ($leaf + ".inbox")) }
        $Keeps += (Full $dest)
        Say ("Kept a copy of the vault in " + $dest + ".")
    } catch {
        Say "The copy of the vault did not work, so nothing was removed."
        Finish 1
    }
}

# 3. The locked box: this install's own projects in it (named by the ids it
# issued), then the box itself through BuildBot's guard and claim count,
# while BuildBot's own Python and records are still there to do it. The
# saved entries go later, after the vault (step 4).
if ($Facts -and ($Facts.box -or $Facts.box_shared -or $BoxProjects -gt 0)) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $raw = @() | & $VenvPy -m puffpai.remove_cli --only box --yes
        foreach ($line in @($raw)) { if ($line -and -not ([string]$line).StartsWith("{")) { Say $line } }
        try {
            $got = (@($raw) | Select-Object -Last 1) | ConvertFrom-Json
            $left = [int]$got.box_projects_left
            if ($left -gt 0) { Say ("" + $left + " of the apps and tools this BuildBot put in the locked box could not be taken out. Run Remove BuildBot again to try once more.") }
        } catch { }
    } finally { $ErrorActionPreference = $prev }
}

# 4. Delete. Links are removed as links and never entered; a file Windows
# still holds is tried again once a second for 30 seconds.
function Remove-Link ($item) {
    if ($item.PSIsContainer) { [System.IO.Directory]::Delete($item.FullName, $false) }
    else { [System.IO.File]::Delete($item.FullName) }
}
# A folder about to be listed must still be a plain folder: one swapped for a
# link or junction since it was looked at stops the delete (never entered).
function Assert-Plain ($dir) {
    $it = Get-Item -LiteralPath $dir -Force -ErrorAction SilentlyContinue
    if ($it -and ($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { throw ("PUFFPAI-CHANGED|" + $dir) }
}
function With-Retry ($where, [scriptblock]$work) {
    $until = (Get-Date).AddSeconds(30)
    while ($true) {
        try { & $work; return }
        catch {
            if (-not (Get-Item -LiteralPath $where -Force -ErrorAction SilentlyContinue)) { return }
            if ((Get-Date) -ge $until) { throw ("PUFFPAI-LOCKED|" + $where) }
            Start-Sleep -Seconds 1
        }
    }
}
function Remove-Tree ($dir) {
    Assert-Plain $dir
    foreach ($link in @(Get-ChildItem -LiteralPath $dir -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue)) {
        $l = $link
        With-Retry $l.FullName { Remove-Link $l }
    }
    Assert-Plain $dir
    foreach ($item in @(Get-ChildItem -LiteralPath $dir -Force -Attributes !ReparsePoint)) {
        if (Is-Kept $item.FullName) { continue }
        if ($item.PSIsContainer) { Remove-Tree $item.FullName }
        else {
            $f = $item.FullName
            With-Retry $f { [System.IO.File]::SetAttributes($f, [System.IO.FileAttributes]::Normal); [System.IO.File]::Delete($f) }
        }
    }
    if (-not (Holds-Kept $dir)) {
        $d = $dir
        With-Retry $d { [System.IO.File]::SetAttributes($d, [System.IO.FileAttributes]::Directory); [System.IO.Directory]::Delete($d, $false) }
    }
}
# The order leaves a BuildBot that Remove can finish at every step: the
# program files first, then the apps, then the settings, then the vault (what
# cannot come back goes last), then the signs a second run needs to know the
# folders are BuildBot's, and the removal files themselves.
$FolderLast = @("Remove BuildBot.cmd", "remove-buildbot.ps1", ".puffpai-install.json", "install.ps1",
                "install.sh", "src\puffpai\__init__.py", "venv")
$StateLast = @("buildbot.env", "build_id.json", "config\build_manifest.json", "api_key.txt",
               "addition_checks.jsonl", "running.json", "pairing")
$RootOf = @{}
foreach ($r in $Roots) { $RootOf[$r[0]] = $r[1] }
$F = $RootOf["folder"]; $S = $RootOf["state"]; $A = $RootOf["apps"]
$VaultParts = @()
if ($VaultGoes) {
    foreach ($v in @($VaultDir, ($VaultDir + ".inbox"))) {
        if ((Test-Path -LiteralPath $v) -and (Is-Covered $v)) { $VaultParts += (Full $v) }
    }
}
$OwnApps = @($Roots | Where-Object { $_[0] -eq "app" } | ForEach-Object { $_[1] })
$Phases = New-Object System.Collections.ArrayList
if ($F) {
    $skip = @($VaultParts) + @($OwnApps)
    foreach ($x in @($S, $A)) { if ($x) { $skip += (Full $x) } }
    foreach ($n in $FolderLast) { $skip += (Full (Join-Path $F $n)) }
    [void]$Phases.Add([pscustomobject]@{ Kind = "folder"; Path = $F; Skip = $skip })
}
if ($A) { [void]$Phases.Add([pscustomobject]@{ Kind = "apps"; Path = $A; Skip = @($VaultParts) }) }
foreach ($o in $OwnApps) { [void]$Phases.Add([pscustomobject]@{ Kind = "app"; Path = $o; Skip = @($VaultParts) }) }
if ($OwnApps.Count -gt 0 -and -not $A) { [void]$Phases.Add([pscustomobject]@{ Kind = "apps_empty"; Path = (Full $AppsRoot); Skip = @() }) }
if ($S) {
    $skip = @($VaultParts) + @($OwnApps)
    if ($A) { $skip += (Full $A) }
    foreach ($n in $StateLast) { $skip += (Full (Join-Path $S $n)) }
    [void]$Phases.Add([pscustomobject]@{ Kind = "state"; Path = $S; Skip = $skip })
}
foreach ($v in $VaultParts) { [void]$Phases.Add([pscustomobject]@{ Kind = "vault"; Path = $v; Skip = @() }) }
# The saved entries (the vault's own among them) only after the vault folder.
[void]$Phases.Add([pscustomobject]@{ Kind = "keyring"; Path = ""; Skip = @() })
if ($S) { [void]$Phases.Add([pscustomobject]@{ Kind = "state"; Path = $S; Skip = @() }) }
if ($F) { [void]$Phases.Add([pscustomobject]@{ Kind = "folder"; Path = $F; Skip = @() }) }
function Say-Left {
    $lines = @()
    foreach ($r in $Roots) {
        if (-not (Get-Item -LiteralPath $r[1] -Force -ErrorAction SilentlyContinue)) { continue }
        if ($r[0] -eq "folder") { $lines += ("  the rest of the BuildBot folder: " + $r[1]) }
        elseif ($r[0] -eq "state") { $lines += ("  your bot's settings, books and chats: " + $r[1]) }
        elseif ($r[0] -eq "app") { $l = ("  the apps BuildBot added: " + (Full $AppsRoot)); if ($lines -notcontains $l) { $lines += $l } }
        else { $lines += ("  the apps BuildBot added: " + $r[1]) }
    }
    $v = @($VaultParts | Where-Object { Test-Path -LiteralPath $_ })
    if ($v.Count -gt 0) { $lines += ("  your vault: " + $v[0]) }
    if ($lines.Count -gt 0) { Say "Still here:"; foreach ($l in $lines) { Say $l } }
}
Write-Host -NoNewline "Deleting ..."
try {
    foreach ($ph in $Phases) {
        if ($ph.Kind -eq "keyring") {
            if ($Facts -and $SavedCount -gt 0 -and (Test-Path -LiteralPath $VenvPy)) {
                $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                try { @() | & $VenvPy -m puffpai.remove_cli --only keyring --yes | Out-Null } finally { $ErrorActionPreference = $prev }
            }
            continue
        }
        if ($ph.Kind -eq "apps_empty") {
            # rmdir never deletes anything inside a folder: only an empty one goes.
            $it = Get-Item -LiteralPath $ph.Path -Force -ErrorAction SilentlyContinue
            if ($it -and -not ($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -and -not (Root-Problem $ph.Path "")) {
                try { [System.IO.Directory]::Delete($ph.Path, $false) } catch { }
            }
            continue
        }
        $script:Later = @($ph.Skip)
        $script:NotKept = Not-Kept-For $ph.Kind
        if (Get-Item -LiteralPath $ph.Path -Force -ErrorAction SilentlyContinue) { Remove-Tree $ph.Path }
        $script:NotKept = ""
    }
    $script:Later = @()
} catch {
    $script:Later = @()
    $script:NotKept = ""
    $m = [string]$_.Exception.Message
    $where = $Repo
    if ($m.StartsWith("PUFFPAI-LOCKED|")) { $where = Split-Path -Parent $m.Substring(15) }
    Write-Host ""
    if ($m.StartsWith("PUFFPAI-CHANGED|")) {
        Say ("Deleting stopped part way, because something in " + $m.Substring(16) + " changed while it was being deleted, so BuildBot left it alone.")
    } else {
        Say ("Deleting stopped part way, because Windows is still using a file in " + $where + ".")
    }
    Say-Left
    Say ("Close the program that uses it, or restart the computer, then double-click Remove BuildBot.cmd in " + $Repo + " again to finish.")
    Finish 1
}
Write-Host " done"
Say "BuildBot is removed."
Finish 0
'@

$removeCmd = @"
@echo off
rem $Marker. Double-click to remove BuildBot from this computer. It asks first.
cd /d "%TEMP%"
setlocal
title Remove BuildBot
set "PUFFPAI_REMOVE_PS1=%TEMP%\puffpai-remove-%RANDOM%%RANDOM%.ps1"
copy /y "%~dp0remove-buildbot.ps1" "%PUFFPAI_REMOVE_PS1%" >nul 2>&1
if errorlevel 1 (
  echo The removal file is missing from this folder. Run install.cmd again, then try once more.
  if not "%PUFFPAI_NO_PAUSE%"=="1" pause
  exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%PUFFPAI_REMOVE_PS1%" -InstallRoot "%~dp0." %* & exit
"@

# The removal script's paths as single-quoted PowerShell literals.
function Expand-RemoveTemplate ($text) {
    function Lit ($v) { return ([string]$v).Replace("'", "''") }
    $text.Replace("__RM_REPO__", (Lit $RepoDir)).
          Replace("__RM_STATE__", (Lit $State)).
          Replace("__RM_DATA__", (Lit $DataDir)).
          Replace("__RM_ENVFILE__", (Lit $EnvFile))
}

# The .cmd files. Batch loads the env file itself for Set up and puffpai, so
# neither needs PowerShell at all; values are quoted in "set" so a path with
# an & in it survives.
$loadEnvCmd = 'for /f "usebackq eol=# tokens=1* delims==" %%A in ("__ENVFILE__") do set "%%A=%%B"'

$startCmd = @"
@echo off
rem $Marker. Double-click to start your bot and open its web page.
setlocal
title Start your bot
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-start.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%PUFFPAI_NO_PAUSE%"=="1" pause
exit /b %RC%
"@

$stopCmd = @"
@echo off
rem $Marker. Double-click to stop your bot and its web page.
setlocal
title Stop your bot
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-stop.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%PUFFPAI_NO_PAUSE%"=="1" pause
exit /b %RC%
"@

$setupCmd = @"
@echo off
rem $Marker. Double-click to set up your bot by answering questions in this window.
setlocal
title Set up BuildBot
if not exist "__ENVFILE__" (
  echo Your bot's settings file is missing. Double-click install.cmd to set BuildBot up again.
  if not "%PUFFPAI_NO_PAUSE%"=="1" pause
  exit /b 1
)
$loadEnvCmd
"__VENVEXE__" setup %*
set "RC=%ERRORLEVEL%"
if not "%PUFFPAI_NO_PAUSE%"=="1" pause
exit /b %RC%
"@

$shimCmd = @"
@echo off
rem $Marker. The puffpai command for this copy of BuildBot, with its own
rem settings loaded. Use it from a Command Prompt in this folder: puffpai where
setlocal
$loadEnvCmd
"__VENVEXE__" %*
exit /b %ERRORLEVEL%
"@

function Expand-Template ($text) {
    $text.Replace("__LOADENV__", $loadEnvPs).
          Replace("__ENVFILE__", $EnvFile).
          Replace("__PORT__", [string]$Port).
          Replace("__DASHPORT__", [string]$DashboardPort).
          Replace("__VENVEXE__", (Join-Path $VenvDir "Scripts\puffpai.exe")).
          Replace("__DASHDIR__", $DashDir).
          Replace("__REPO__", $RepoDir)
}

function Write-Launcher ($path, $body) {
    $body = ($body -replace "`r?`n", "`r`n")
    if ($DryRun) {
        Say ("  [dry-run] would write " + $path + ":")
        foreach ($line in ($body -split "`r`n")) { Say ("      " + $line) }
        return
    }
    if ($path -like "*.ps1") {
        # UTF-8 with a byte order mark: a folder name with letters like e-acute
        # must reach the script as it is (Windows PowerShell reads a BOM file
        # as UTF-8, and a file without one as the old code page).
        [System.IO.File]::WriteAllText($path, ($body + "`r`n"), (New-Object System.Text.UTF8Encoding($true)))
    } else {
        Set-Content -Path $path -Value $body -Encoding ASCII
    }
}

# The launchers are named for the bot once it has a name. Before the wizard has
# run, that is "BuildBot".
function Get-BotName {
    if ($DryRun -or -not (Test-Path $VenvPy)) { return "BuildBot" }
    $n = ""
    try {
        $env:PUFFPAI_DATA_DIR = $DataDir; $env:PUFFPAI_STATE_DIR = $State
        $ErrorActionPreference = "Continue"
        $n = (& $VenvPy -c "from puffpai import bot_identity as b; n = (b.bot_name(fallback='') or '').strip(); print(n if n.lower() not in b._shipped_names() else '')" 2>$null | Select-Object -Last 1)
    } catch { }
    $n = ([string]$n -replace '[^A-Za-z0-9 _.-]', '').Trim()
    if ($n.Length -gt 40) { $n = $n.Substring(0, 40).Trim() }
    if (-not $n) { return "BuildBot" }
    return $n
}
$BotName = Get-BotName
$StartCmdPath = Join-Path $RepoDir ("Start " + $BotName + ".cmd")
$StopCmdPath  = Join-Path $RepoDir ("Stop " + $BotName + ".cmd")

Write-Launcher $RunSetup   (Expand-Template $setupTemplate)
Write-Launcher $RunBackend (Expand-Template $backendTemplate)
Write-Launcher $RunDash    (Expand-Template $dashTemplate)
Write-Launcher $RunStart   (Expand-Template $startTemplate)
Write-Launcher $RunStop    (Expand-Template $stopTemplate)
Write-Launcher (Join-Path $RepoDir "Start BuildBot.cmd") (Expand-Template $startCmd)
Write-Launcher (Join-Path $RepoDir "Stop BuildBot.cmd")  (Expand-Template $stopCmd)
if ($BotName -ne "BuildBot") {
    Write-Launcher $StartCmdPath (Expand-Template $startCmd)
    Write-Launcher $StopCmdPath  (Expand-Template $stopCmd)
}
Write-Launcher $SetupCmdPath (Expand-Template $setupCmd)
Write-Launcher (Join-Path $RepoDir "remove-buildbot.ps1") (Expand-RemoveTemplate $removeTemplate)
Write-Launcher (Join-Path $RepoDir "Remove BuildBot.cmd") (Expand-Template $removeCmd)
Write-Launcher $ShimCmdPath (Expand-Template $shimCmd)
Write-Launcher $Pointer    (@{ env_file = $EnvFile } | ConvertTo-Json)

Say ("  In " + $RepoDir + ":")
$shortcuts = @(
    @(("Start " + $BotName + ".cmd"), "starts your bot and opens its web page"),
    @(("Stop " + $BotName + ".cmd"), "stops it"),
    @("Set up BuildBot.cmd", "asks the setup questions in a window instead of the web page"),
    @("Remove BuildBot.cmd", "takes BuildBot off this computer (it asks first)"),
    @("puffpai.cmd", "for people who like typing commands (try: puffpai.cmd where)")
)
$width = ($shortcuts | ForEach-Object { $_[0].Length } | Measure-Object -Maximum).Maximum + 2
foreach ($s in $shortcuts) { Say ("    " + $s[0].PadRight($width) + $s[1]) }

# --- what to do next -------------------------------------------------------
Say ""
Say "BuildBot can set up a locked box for outside code later, when you pick something that needs it."
Say ""
if (-not $OllamaOk) {
    Say "Note: Ollama is not installed yet. Setup still works, but your bot cannot"
    Say "answer until Ollama is on this computer (https://ollama.com/download/windows)."
    Say ""
}

if ($DryRun) {
    Say "Practice run finished. Nothing was changed."
    exit 0
}

if ($CanAsk) {
    Say "Where do you want to set up your bot?"
    if ($DashboardBuilt) {
        Say "  1) In your web browser (opens now)"
        Say "  2) Right here in this window"
    } else {
        Say "  The web page is not built, so setup happens right here in this window."
    }
}

function Import-InstallEnv {
    foreach ($line in (Get-Content $EnvFile)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), "Process")
        }
    }
}

$choice = ""
if ($CanAsk) {
    if ($DashboardBuilt) {
        for ($i = 0; $i -lt 3 -and $choice -notin @("1", "2"); $i++) { $choice = (Read-Host "Type 1 or 2").Trim() }
    } elseif (Ask-YesNo "Start the setup questions now?") { $choice = "2" }
}

if ($choice -eq "1") {
    & $RunStart
    exit $LASTEXITCODE
} elseif ($choice -eq "2") {
    Import-InstallEnv
    & (Join-Path $VenvDir "Scripts\puffpai.exe") setup
    exit $LASTEXITCODE
}

# No answer (or nobody to ask): say how to do either later. Both lead to the
# same questions.
Say "To set up your bot, either:"
if ($DashboardBuilt) {
    Say ("  - in your web browser: double-click ""Start " + $BotName + ".cmd""")
}
Say  "  - right here in a window: double-click ""Set up BuildBot.cmd"""
Say ("  Both are in " + $RepoDir)
Say "  From a Command Prompt in that folder, ""puffpai.cmd setup"" does the same, and"
Say "  ""puffpai.cmd where"" shows which folders and ports this copy uses."
exit 0
