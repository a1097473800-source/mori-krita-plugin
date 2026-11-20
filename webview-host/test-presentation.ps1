param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$destination = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$runtime = Join-Path $PSScriptRoot '..\mori_krita\webview-host'
foreach ($name in @('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll')) {
    Copy-Item -LiteralPath (Join-Path $runtime $name) -Destination $destination -Force
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$program = Join-Path $destination 'HostPresentationTests.exe'
& $compiler /nologo /target:exe /platform:x64 /optimize+ /nowarn:4014 /main:HostPresentationTests "/out:$program" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "/reference:$destination\Microsoft.Web.WebView2.Core.dll" "/reference:$destination\Microsoft.Web.WebView2.WinForms.dll" (Join-Path $PSScriptRoot 'MoriWebHost.cs') (Join-Path $PSScriptRoot 'HostPresentationTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Host presentation compilation failed.' }
& $program $destination
if ($LASTEXITCODE -ne 0) { throw 'Host presentation regression failed.' }
