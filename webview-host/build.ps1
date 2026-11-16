param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\mori_krita\webview-host')
)
$ErrorActionPreference = 'Stop'
$webViewVersion = '1.0.4314.35'
$packageSha256 = '90778DEB1B77E36292828B4B5833C9255C27533F40E0DDD55891AE718944EDC4'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework x64 编译器未找到。' }
$dependencyRoot = Join-Path $PSScriptRoot 'build\deps'
$sdkRoot = Join-Path $dependencyRoot $webViewVersion
New-Item -ItemType Directory -Path $dependencyRoot, $OutputDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $sdkRoot 'lib\net462\Microsoft.Web.WebView2.Core.dll'))) {
    $package = Join-Path $dependencyRoot 'webview2.nupkg'
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$webViewVersion/microsoft.web.webview2.$webViewVersion.nupkg" -OutFile $package -TimeoutSec 60
    if ((Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash -ne $packageSha256) {
        throw 'WebView2 SDK 包校验失败，未执行编译。'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($package, $sdkRoot)
}
$core = Join-Path $sdkRoot 'lib\net462\Microsoft.Web.WebView2.Core.dll'
$forms = Join-Path $sdkRoot 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'
$destination = [System.IO.Path]::GetFullPath($OutputDirectory)
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /nowarn:4014 "/out:$destination\MoriWebHost.exe" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "/reference:$core" "/reference:$forms" (Join-Path $PSScriptRoot 'MoriWebHost.cs')
if ($LASTEXITCODE -ne 0) { throw 'WebView2 宿主编译失败。' }
Copy-Item -LiteralPath $core, $forms -Destination $destination -Force
Copy-Item -LiteralPath (Join-Path $sdkRoot 'runtimes\win-x64\native\WebView2Loader.dll') -Destination $destination -Force
Copy-Item -LiteralPath (Join-Path $sdkRoot 'LICENSE.txt') -Destination (Join-Path $destination 'WebView2-LICENSE.txt') -Force
$versionSource = Join-Path $PSScriptRoot '..\mori_krita\version.py'
$version = [regex]::Match([IO.File]::ReadAllText($versionSource), '(?m)^\s*VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$').Groups[1].Value
if (-not $version) { throw '插件版本无效，未完成宿主构建。' }
$files = foreach ($name in @('MoriWebHost.exe','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','WebView2-LICENSE.txt')) {
    $file = Join-Path $destination $name
    [ordered]@{name=$name;size=(Get-Item -LiteralPath $file).Length;sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$release = [ordered]@{schemaVersion=1;hostVersion=$version;shutdownProtocol=2;files=@($files)}
[IO.File]::WriteAllText((Join-Path $destination 'host-release.json'), ($release | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
Write-Output "WebView2 宿主已构建：$destination"
