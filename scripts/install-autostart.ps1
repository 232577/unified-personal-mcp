[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [string]$BundlePath = (Split-Path -Parent $PSScriptRoot),
    [ValidateSet('Enable', 'Disable', 'Status')][string]$Action = 'Enable',
    [string]$ExpectedVersion,
    [ValidatePattern('^UnifiedPersonalMCP-[A-Za-z0-9_-]+$')]
    [string]$TaskName = 'UnifiedPersonalMCP-AutoConnect'
)

$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path -LiteralPath $BundlePath).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$python = Join-Path $bundle 'resources/python/python.exe'
$entry = Join-Path $bundle 'app/run.py'
foreach ($required in @($python, $entry)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw 'Complete portable package required.' }
}
$cliArgs = @('-B', '-s', $entry, ('autostart-' + $Action.ToLowerInvariant()),
    '--config', $config, '--task-name', $TaskName)
if ($Action -eq 'Enable') {
    if (-not $ExpectedVersion) { throw 'Supply -ExpectedVersion to validate the installation version.' }
    $cliArgs += @('--bundle', $bundle, '--expected-version', $ExpectedVersion)
}
# The packaged manager validates hashes and isolated startup before changing the
# login target. It backs up the prior task and never starts or stops a live service.
& $python @cliArgs
if ($LASTEXITCODE -ne 0) { throw 'Automatic startup management failed; inspect the diagnostic above.' }
