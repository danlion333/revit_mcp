<#
.SYNOPSIS
  Launch Revit with the RevitMCP bridge auto-started and wait until it listens.

.DESCRIPTION
  For unattended use (CI, a VM driven by an agent). Sets REVIT_MCP_AUTOSTART so the
  extension's startup.py starts the bridge, opens the given model, and returns once the
  bridge port accepts connections. Revit is left running.

  Must run in an interactive desktop session: Revit needs a desktop, and a session
  without one (a plain SSH session on Windows) cannot show its window.

  Revit may still show a modal dialog after this returns (a missing-links warning, the
  "hardware acceleration disabled" notice on machines without a GPU). While a dialog is
  up, every bridge call returns a `busy` error; dismiss it and the calls go through.

.EXAMPLE
  .\scripts\launch-revit-with-bridge.ps1 -Model "C:\Projects\Tower.rvt"
#>
param(
  [Parameter(Mandatory = $true)][string]$Model,
  [string]$RevitExe = 'C:\Program Files\Autodesk\Revit 2026\Revit.exe',
  [int]$Port = 9877,
  [int]$TimeoutSec = 600
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $RevitExe)) { throw "Revit not found at $RevitExe" }
if (-not (Test-Path -LiteralPath $Model)) { throw "Model not found at $Model" }

$env:REVIT_MCP_AUTOSTART = '1'
$env:REVIT_MCP_PORT = "$Port"
# Start-Process (ShellExecute) on purpose: with handle inheritance Revit would keep
# this script's stdout open for the life of the process.
$proc = Start-Process -FilePath $RevitExe -ArgumentList @('/nosplash', ('"' + $Model + '"')) -PassThru
Write-Output "Revit pid $($proc.Id), waiting for the bridge on port $Port"

$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
  if ($proc.HasExited) { throw "Revit exited with code $($proc.ExitCode) before the bridge started" }
  if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Output "bridge listening on 127.0.0.1:$Port (log: $env:LOCALAPPDATA\RevitMCP\bridge.log)"
    exit 0
  }
  Start-Sleep -Seconds 2
}
throw "bridge did not start listening within ${TimeoutSec}s; see $env:LOCALAPPDATA\RevitMCP\bridge.log"
