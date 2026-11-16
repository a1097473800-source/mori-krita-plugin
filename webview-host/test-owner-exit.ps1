param(
    [string]$HostExecutable = (Join-Path $PSScriptRoot '..\mori_krita\webview-host\MoriWebHost.exe'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\qa\host-owner-exit')
)
$ErrorActionPreference = 'Stop'
$destination = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$program = Join-Path $destination 'OwnerExitTests.exe'
& $compiler /nologo /target:exe /platform:x64 /optimize+ /nowarn:4014 "/out:$program" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'OwnerExitTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Owner-exit test compilation failed.' }
& $program ([IO.Path]::GetFullPath($HostExecutable)) $destination
if ($LASTEXITCODE -ne 0) { throw 'Owner-exit regression failed. See owner-exit-qa.json.' }
