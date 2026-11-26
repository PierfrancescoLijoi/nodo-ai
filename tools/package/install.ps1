# Installs (or removes) NODO AI for the current user. No administrator rights, no other software.
param([switch]$Uninstall, [string]$Payload = (Join-Path $PSScriptRoot 'payload.zip'))
$ErrorActionPreference = 'Stop'
$target = Join-Path $env:LOCALAPPDATA 'Programs\NODO'
$menu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$link = Join-Path $menu 'NODO AI.lnk'

if ($Uninstall) {
    if (Test-Path $link) { Remove-Item $link -Force }
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    Write-Output 'NODO AI removed. Your meetings and models in %LOCALAPPDATA%\NODO were not touched.'
    return
}
if (-not (Test-Path $Payload)) { throw "payload.zip not found next to the installer ($Payload)" }
if (Test-Path $target) { Remove-Item $target -Recurse -Force }   # the program only; data lives elsewhere
New-Item -ItemType Directory -Force $target | Out-Null
Expand-Archive -Path $Payload -DestinationPath $target -Force
New-Item -ItemType Directory -Force $menu | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
$shortcut.Arguments = '/c "' + (Join-Path $target 'NODO.cmd') + '"'
$shortcut.WorkingDirectory = $target
$shortcut.WindowStyle = 7
$shortcut.Description = 'NODO AI: local meeting memory'
$shortcut.Save()
Write-Output "NODO AI installed in $target. Start it from the Start menu."
