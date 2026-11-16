param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\qa\host-policy'))
$ErrorActionPreference = 'Stop'
$sdkRoot = Join-Path $PSScriptRoot 'build\deps\1.0.4314.35\lib\net462'
$core = Join-Path $sdkRoot 'Microsoft.Web.WebView2.Core.dll'
$forms = Join-Path $sdkRoot 'Microsoft.Web.WebView2.WinForms.dll'
if (-not (Test-Path -LiteralPath $core)) { throw 'Run build.ps1 once to prepare the pinned SDK.' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$destination = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$program = Join-Path $destination 'HostPolicyTests.exe'
& $compiler /nologo /target:exe /platform:x64 /optimize+ /nowarn:4014 /main:HostPolicyTests "/out:$program" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "/reference:$core" "/reference:$forms" (Join-Path $PSScriptRoot 'MoriWebHost.cs') (Join-Path $PSScriptRoot 'HostPolicyTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Host policy compilation failed.' }
$report = & $program
if ($LASTEXITCODE -ne 0) { throw 'Host policy tests failed.' }
[System.IO.File]::WriteAllText((Join-Path $destination 'host-policy-qa.json'), [string]$report, [System.Text.UTF8Encoding]::new($false))
Write-Output $report
