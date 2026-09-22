[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [ValidatePattern('^UnifiedPersonalMCP-[A-Za-z0-9_-]+$')]
    [string]$TaskName = 'UnifiedPersonalMCP-AutoConnect',
    [switch]$LocalOnly
)

$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path -LiteralPath $BundlePath).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$launcher = Join-Path $bundle 'UnifiedPersonalMCP.exe'
$python = Join-Path $bundle 'resources/python/pythonw.exe'
$entry = Join-Path $bundle 'app/run.py'
$assets = Join-Path $bundle 'resources'
foreach ($required in @($launcher, $python, $entry, $config)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
    }
}
# Windows file names cannot contain quotes; reject them before building argv.
if ($config.Contains('"') -or $bundle.Contains('"')) { throw 'Invalid path' }
$description = 'Unified Personal MCP automatic connection (managed by install-autostart.ps1).'
$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) {
    throw 'This task name is already used by an unrelated task.'
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$arguments = '-B -s "' + $entry + '" serve --config "' + $config + '" --assets "' + $assets + '"'
if (-not $LocalOnly) { $arguments += ' --connect' }
# Own the service process itself: stopping an outer launcher leaves its child running.
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $bundle
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$trigger.Delay = 'PT20S'
# Interactive login preserves access to the owner's desktop and DPAPI secrets.
# It neither stores a Windows password nor changes Windows automatic login.
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description $description -Force | Out-Null
# Registration intentionally does not interrupt or replace a running service.
$saved = Get-ScheduledTask -TaskName $TaskName -TaskPath '\'
[pscustomobject]@{
    TaskName = $saved.TaskName
    State = [string]$saved.State
    Executable = $saved.Actions.Execute
    Arguments = $saved.Actions.Arguments
    Startup = '20 seconds after this Windows user logs in'
    Connection = $(if ($LocalOnly) { 'Local only' } else { 'OpenAI tunnel' })
    StartedNow = $false
}
