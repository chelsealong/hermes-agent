"""Transaction-boundary regression for Windows venv recreation (#83149).

Review finding on PR #83194 (egilewski): the rollback source (the parked
previous venv) was deleted as soon as ``Install-Venv`` saw a working
interpreter in the replacement — but ``Install-Dependencies`` is a separate,
later stage (a separate *process* under the stage-per-process bootstrap) and
every dependency tier or the baseline-import gate can still fail after that
point. Deleting the backup early re-creates exactly the availability failure
the transactional recreate exists to prevent.

The contract locked here:

* ``Install-Venv`` records the parked backup in ``venv.pending-backup``
  instead of deleting it, and its stale-tree sweep excludes that backup.
* ``Install-Dependencies`` restores the previous venv on failure
  (``Restore-VenvBackup``) and commits the cleanup only after the
  baseline-import gate passes (``Complete-VenvTransaction``).

The script only runs on Windows, so Linux CI locks the contract at the
source level, same approach as tests/test_install_ps1_venv_recreate_safety.py.

``test_retry_before_dependency_stage_keeps_original_rollback_source`` below
adds a second, behavioural contract (#103751): a retried ``Install-Venv``
before ``Install-Dependencies`` ever validated the first replacement must
not treat that unvalidated replacement as a rollback source. It extracts the
real ``Install-Venv`` / ``Get-PendingVenvBackup`` / ``Restore-VenvBackup``
bodies with the PowerShell AST -- unmodified -- and runs them against a real
temp directory, so unlike the tests above it needs a working ``pwsh``/
``powershell`` and is not Windows-only (no Windows-specific API is on this
path).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_PS1 = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
POWERSHELL = next(
    (candidate for candidate in ("pwsh", "powershell") if shutil.which(candidate)),
    None,
)


def _function_body(source: str, function_name: str) -> str:
    start = source.index(f"function {function_name}")
    opening_brace = source.index("{", start)
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace : index + 1]
    raise AssertionError(f"unterminated function: {function_name}")


def _source() -> str:
    return INSTALL_PS1.read_text(encoding="ascii")


def test_install_venv_does_not_delete_backup_before_dependency_stage() -> None:
    """The parked previous venv must survive Install-Venv's success path."""
    body = _function_body(_source(), "Install-Venv")

    # The success path records the rollback source instead of deleting it.
    assert "venv.pending-backup" in body
    # The only backup deletion allowed inside Install-Venv is the *rollback*
    # rename in the catch block; a Remove-Item of the backup must not appear.
    assert "Remove-Item -LiteralPath $venvBackupName" not in body


def test_install_venv_stale_sweep_excludes_current_backup() -> None:
    """The venv.stale.* sweep must not delete this run's rollback source."""
    body = _function_body(_source(), "Install-Venv")

    sweep_at = body.index('Get-ChildItem -Directory -Filter "venv.stale.*"')
    window = body[sweep_at : sweep_at + 400]
    assert "$_.Name -ne $venvBackupName" in window, (
        "the stale-tree sweep must exclude the backup parked by this run"
    )


def test_install_dependencies_restores_backup_on_failure() -> None:
    """A failed dependency tier or import gate must restore the parked venv."""
    body = _function_body(_source(), "Install-Dependencies")

    assert "Restore-VenvBackup" in body
    catch_at = body.index("Restore-VenvBackup")
    assert "throw" in body[catch_at : catch_at + 400], (
        "rollback must rethrow the original failure after restoring"
    )


def test_install_dependencies_commits_only_after_import_gate() -> None:
    """Backup cleanup must come after the baseline-import verification."""
    body = _function_body(_source(), "Install-Dependencies")

    import_gate = body.index("Baseline imports verified in venv")
    commit = body.index("Complete-VenvTransaction")
    assert import_gate < commit, (
        "the venv transaction must commit only after imports prove the "
        "replacement usable"
    )


def test_restore_helper_parks_failed_replacement_and_restores_previous() -> None:
    body = _function_body(_source(), "Restore-VenvBackup")

    park = body.index("venv.failed.")
    restore = body.index('-NewName "venv"')
    assert park < restore, (
        "the failed replacement must be parked before the previous venv is "
        "renamed back into place"
    )


def test_commit_helper_deletes_backup_and_clears_marker() -> None:
    body = _function_body(_source(), "Complete-VenvTransaction")

    assert "Remove-Item" in body
    assert "venv.pending-backup" in body


# Adapted from the fault-injection reproduction in issue #103751. Parses the
# named production functions out of install.ps1 with the PowerShell AST and
# invokes them unmodified; only the interpreter/process-enumeration/sleep
# primitives are stubbed. Renames, marker reads/writes, and the stale-tree
# sweep all run against real filesystem entries under a synthetic case dir.
_RETRY_RECONCILIATION_SCRIPT = r"""
param([Parameter(Mandatory=$true)][string]$Repository)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$caseDir = Join-Path $root ('case-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $caseDir | Out-Null
$script:InstallDir = $caseDir
$NoVenv = $false
$script:UvCmd = 'synthetic-uv-never-executed.exe'
$source = Join-Path $Repository 'scripts/install.ps1'
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Installer parse failed' }
$names = @('Install-Venv', 'Get-PendingVenvBackup', 'Restore-VenvBackup', 'Complete-VenvTransaction')
foreach ($name in $names) {
    $matches = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true))
    if ($matches.Count -ne 1) { throw "Expected one complete function: $name" }
    Invoke-Expression $matches[0].Extent.Text
}
function Write-Info { param($Message) Write-Output "INFO $Message" }
function Write-Warn { param($Message) Write-Output "WARN $Message" }
function Write-Success { param($Message) Write-Output "SUCCESS $Message" }
function Resolve-AvailablePythonVersion { [pscustomobject]@{ Path='synthetic-python'; Version='3.11' } }
function schtasks { $global:LASTEXITCODE=0 }
function taskkill { $global:LASTEXITCODE=0 }
function Get-CimInstance { @() }
function Start-Sleep { }
function New-Object {
    param([string]$TypeName)
    if ($TypeName -eq 'System.Diagnostics.ProcessStartInfo') {
        return [pscustomobject]@{ FileName=''; Arguments=''; WorkingDirectory=''; UseShellExecute=$false; CreateNoWindow=$false; RedirectStandardOutput=$false; RedirectStandardError=$false }
    }
    if ($TypeName -ne 'System.Diagnostics.Process') { throw "Unexpected object type $TypeName" }
    $reader = [pscustomobject]@{}
    $reader | Add-Member ScriptMethod ReadToEndAsync { [pscustomobject]@{ Result='' } }
    $process = [pscustomobject]@{ StartInfo=$null; StandardOutput=$reader; StandardError=$reader; ExitCode=0 }
    $process | Add-Member ScriptMethod Start {
        $dir = Join-Path $this.StartInfo.WorkingDirectory 'venv/Scripts'
        [IO.Directory]::CreateDirectory($dir) | Out-Null
        [IO.File]::WriteAllText((Join-Path $dir 'python.exe'), 'synthetic interpreter placeholder')
        [IO.File]::WriteAllText((Join-Path $this.StartInfo.WorkingDirectory 'venv/generation.txt'), 'PARTIAL_NEW_ENV_WITHOUT_DEPENDENCIES')
        return $true
    }
    $process | Add-Member ScriptMethod WaitForExit { }
    $process | Add-Member ScriptMethod Dispose { }
    return $process
}
function Remove-Item {
    [CmdletBinding()] param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)
    $resolved = [IO.Path]::GetFullPath($LiteralPath)
    if (-not $resolved.StartsWith($caseDir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Delete escaped case directory: $resolved" }
    Microsoft.PowerShell.Management\Remove-Item -LiteralPath $resolved -Recurse:$Recurse -Force:$Force -ErrorAction Stop
}
$oldVenv = Join-Path $caseDir 'venv'
[IO.Directory]::CreateDirectory($oldVenv) | Out-Null
[IO.File]::WriteAllText((Join-Path $oldVenv 'generation.txt'), 'ORIGINAL_WORKING_ENV')
Write-Output 'FIRST VENV STAGE'
Install-Venv
$originalBackup = Get-PendingVenvBackup
if (-not (Test-Path -LiteralPath (Join-Path $caseDir "$originalBackup/generation.txt"))) {
    Write-Output 'SETUP FAILED: first stage did not preserve the original venv'
    exit 2
}
Write-Output 'SIMULATE INTERRUPTION BEFORE DEPENDENCY STAGE; RETRY VENV STAGE'
Install-Venv
$originalStillExists = Test-Path -LiteralPath (Join-Path $caseDir $originalBackup)
Write-Output 'SIMULATE DEPENDENCY FAILURE; INVOKE PRODUCTION ROLLBACK'
Restore-VenvBackup
$restored = [IO.File]::ReadAllText((Join-Path $oldVenv 'generation.txt'))
$passed = $originalStillExists -and ($restored -eq 'ORIGINAL_WORKING_ENV')
Write-Output "original_backup_survived_retry=$originalStillExists restored_generation=$restored"
if (-not $passed) { Write-Output 'RED: RETRY DELETED ORIGINAL BACKUP AND RESTORED PARTIAL ENVIRONMENT'; exit 1 }
Write-Output 'GREEN: ORIGINAL WORKING ENVIRONMENT RESTORED'
"""


@pytest.mark.skipif(POWERSHELL is None, reason="needs pwsh or Windows PowerShell")
def test_retry_before_dependency_stage_keeps_original_rollback_source(
    tmp_path: Path,
) -> None:
    """A retry before Install-Dependencies validates B must not lose A.

    Sequence: Install-Venv parks the original working venv A and creates
    replacement B, recording A as the rollback source. Before
    Install-Dependencies ever validates B (simulating an interrupted stage
    host), Install-Venv runs again. It must reconcile the still-open
    transaction rather than parking B as a new backup, overwriting the
    marker, and sweeping A away as an ordinary stale tree. A later dependency
    failure must then restore A, not the never-validated B.
    """
    script_path = tmp_path / "reproduce.ps1"
    script_path.write_text(_RETRY_RECONCILIATION_SCRIPT, encoding="utf-8")
    repo_root = INSTALL_PS1.resolve().parents[1]

    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(script_path), "-Repository", str(repo_root)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        "retry-before-validation left the original rollback source lost or "
        f"restored the wrong generation:\n{result.stdout}\n{result.stderr}"
    )
    assert "GREEN: ORIGINAL WORKING ENVIRONMENT RESTORED" in result.stdout
