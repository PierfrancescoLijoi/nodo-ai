# Builds dist\NODO-Setup.exe: bundle -> payload.zip -> IExpress self-extractor (built into Windows).
param([string]$Python = 'services\api\.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
$work = 'dist\installer'
Remove-Item dist\NODO, $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work | Out-Null
& $Python tools\package\build_bundle.py dist\NODO
if ($LASTEXITCODE -ne 0) { throw 'bundle failed' }
Copy-Item tools\package\install.ps1, tools\package\Uninstall-NODO.cmd dist\NODO
Compress-Archive -Path dist\NODO\* -DestinationPath "$work\payload.zip" -CompressionLevel Optimal
Copy-Item tools\package\install.ps1, tools\package\install.cmd $work
$full = (Resolve-Path $work).Path
$out = Join-Path (Resolve-Path dist).Path 'NODO-Setup.exe'
$sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=
DisplayLicense=
FinishMessage=NODO AI is installed. Open it from the Start menu.
TargetName=$out
FriendlyName=NODO AI Setup
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="install.cmd"
FILE1="install.ps1"
FILE2="payload.zip"
[SourceFiles]
SourceFiles0=$full\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
"@
Set-Content -Path "$work\nodo.sed" -Value $sed -Encoding ASCII
Remove-Item $out -ErrorAction SilentlyContinue
Start-Process -FilePath iexpress.exe -ArgumentList '/N', '/Q', "$full\nodo.sed" -Wait -NoNewWindow
if (-not (Test-Path $out)) { throw 'IExpress did not produce NODO-Setup.exe' }
Get-Item $out | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB)}}
