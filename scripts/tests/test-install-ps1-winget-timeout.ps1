# Smoke tests for the wall-clock timeout guard around winget installs in
# install.ps1 (see #78085 -- the installer could hang forever on "Installing
# ripgrep ... via winget..." with no way to recover).
#
# Run from a PowerShell prompt:
#
#   pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/tests/test-install-ps1-winget-timeout.ps1
#
# This test extracts ONLY the Invoke-WithWallClockTimeout function from
# install.ps1 (via the PowerShell AST, not a hand-copied duplicate) and
# exercises it directly with fake fast/slow script blocks. It deliberately
# does NOT run the whole install.ps1 file (dot-sourcing it would fall
# through to Main() and attempt a real install) or invoke real winget.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$installScript = Join-Path $repoRoot "scripts\install.ps1"

if (-not (Test-Path $installScript)) {
    throw "Could not locate install.ps1 at $installScript"
}

$failures = 0
function Assert-Equal {
    param([AllowNull()][Parameter(Mandatory=$true)] $Expected,
          [AllowNull()][Parameter(Mandatory=$true)] $Actual,
          [Parameter(Mandatory=$true)] [string]$Label)
    if ($Expected -ne $Actual) {
        Write-Host "FAIL: $Label" -ForegroundColor Red
        Write-Host "  expected: $Expected"
        Write-Host "  actual:   $Actual"
        $script:failures++
    } else {
        Write-Host "OK: $Label" -ForegroundColor Green
    }
}
function Assert-True {
    param([Parameter(Mandatory=$true)] $Condition,
          [Parameter(Mandatory=$true)] [string]$Label)
    if (-not $Condition) {
        Write-Host "FAIL: $Label" -ForegroundColor Red
        $script:failures++
    } else {
        Write-Host "OK: $Label" -ForegroundColor Green
    }
}

# -----------------------------------------------------------------------------
# Extract Invoke-WithWallClockTimeout from install.ps1 via the AST and load
# just that function -- proves the shipped source defines it, without
# executing the rest of the (side-effecting) installer script.
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "-- extracting Invoke-WithWallClockTimeout from install.ps1 --"

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installScript, [ref]$tokens, [ref]$parseErrors)
Assert-Equal -Expected 0 -Actual $parseErrors.Count -Label "install.ps1 parses with no syntax errors"

$fnAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq "Invoke-WithWallClockTimeout"
}, $true)

Assert-True ($null -ne $fnAst) -Label "install.ps1 defines Invoke-WithWallClockTimeout"

if (-not $fnAst) {
    Write-Host ""
    Write-Host "FAILED: Invoke-WithWallClockTimeout is missing -- winget installs have no timeout guard (#78085)." -ForegroundColor Red
    exit 1
}

. ([scriptblock]::Create($fnAst.Extent.Text))
Assert-True (Get-Command Invoke-WithWallClockTimeout -ErrorAction SilentlyContinue) -Label "Invoke-WithWallClockTimeout is callable after extraction"

# -----------------------------------------------------------------------------
# Test: a fast script block completes normally and returns its exit code
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "-- fast script block --"
$fastResult = Invoke-WithWallClockTimeout -TimeoutSec 15 -ScriptBlock { 0 }
Assert-Equal -Expected $false -Actual $fastResult.TimedOut -Label "fast job does not time out"
Assert-Equal -Expected 0 -Actual $fastResult.ExitCode -Label "fast job returns its exit code"

# -----------------------------------------------------------------------------
# Test: a hanging script block is killed at the timeout instead of hanging
# forever -- this is the actual bug from #78085 (winget "Installing ripgrep
# ... via winget..." never returning). Uses a short 2s timeout against a
# script block that sleeps far longer, and asserts the CALL ITSELF returns
# well within a generous wall-clock bound instead of blocking indefinitely.
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "-- hanging script block (simulates a stuck winget install) --"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$hangResult = Invoke-WithWallClockTimeout -TimeoutSec 2 -ScriptBlock { Start-Sleep -Seconds 120; 0 }
$sw.Stop()

Assert-Equal -Expected $true -Actual $hangResult.TimedOut -Label "hanging job is reported as timed out"
Assert-Equal -Expected $null -Actual $hangResult.ExitCode -Label "timed-out job has no exit code"
Assert-True ($sw.Elapsed.TotalSeconds -lt 30) -Label "call returns promptly instead of hanging (took $([math]::Round($sw.Elapsed.TotalSeconds, 1))s, must be < 30s for a 2s timeout)"

# -----------------------------------------------------------------------------
# Test: the winget install call site inside Install-SystemPackages actually
# routes through the timeout guard (not just defined-but-unused)
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "-- Install-SystemPackages wiring --"
$installFnAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq "Install-SystemPackages"
}, $true)
Assert-True ($null -ne $installFnAst) -Label "install.ps1 defines Install-SystemPackages"
if ($installFnAst) {
    Assert-True ($installFnAst.Extent.Text -match "Invoke-WithWallClockTimeout") `
        -Label "Install-SystemPackages calls Invoke-WithWallClockTimeout around the winget install"
}

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
Write-Host ""
if ($failures -gt 0) {
    Write-Host "FAILED: $failures assertion(s) failed" -ForegroundColor Red
    exit 1
} else {
    Write-Host "All smoke tests passed." -ForegroundColor Green
    exit 0
}
