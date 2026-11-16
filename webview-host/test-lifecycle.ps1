param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\qa\host-lifecycle'))
$ErrorActionPreference = 'Stop'
$sdkRoot = Join-Path $PSScriptRoot 'build\deps\1.0.4314.35\lib\net462'
$destination = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $destination -Force | Out-Null
foreach ($name in @('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll')) {
    Copy-Item -LiteralPath (Join-Path $sdkRoot $name) -Destination $destination -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'build\deps\1.0.4314.35\runtimes\win-x64\native\WebView2Loader.dll') -Destination $destination -Force
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$program = Join-Path $destination 'HostLifecycleTests.exe'
& $compiler /nologo /target:exe /platform:x64 /optimize+ /nowarn:4014 /main:HostLifecycleTests "/out:$program" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "/reference:$destination\Microsoft.Web.WebView2.Core.dll" "/reference:$destination\Microsoft.Web.WebView2.WinForms.dll" (Join-Path $PSScriptRoot 'MoriWebHost.cs') (Join-Path $PSScriptRoot 'HostLifecycleTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Host lifecycle compilation failed.' }
& $program $destination
if ($LASTEXITCODE -ne 0) { throw 'Host lifecycle regression failed. See host-lifecycle-qa.json.' }
