param(
    [string]$ResourceDirectory = (Join-Path $env:APPDATA 'krita'),
    [switch]$NoEnable,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
if (-not $CheckOnly -and (Get-Process -Name krita -ErrorAction SilentlyContinue)) {
    throw '请先退出 Krita，再运行安装。'
}
$nativeArchitecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if (-not [Environment]::Is64BitOperatingSystem -or $nativeArchitecture -ne 'AMD64') {
    throw '这个安装包适用于Windows x64电脑，请使用64位Krita和对应的安装包。'
}
foreach ($requiredFile in @('installer-support.ps1','mori_krita.desktop','mori_krita\__init__.py','mori_krita\version.py','mori_krita\web\index.html','mori_krita\webview-host\MoriWebHost.exe','mori_krita\webview-host\WebView2Loader.dll','mori_krita\webview-host\Microsoft.Web.WebView2.Core.dll','mori_krita\webview-host\Microsoft.Web.WebView2.WinForms.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $requiredFile) -PathType Leaf)) {
        throw ('插件文件缺失：' + $requiredFile + '。请先完整解压安装包，原插件尚未更改。')
    }
}
foreach ($folder in @('web','webview-host','icons')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot ('mori_krita\' + $folder)) -PathType Container)) {
        throw ('插件资源缺失：' + $folder + '。请先完整解压安装包，原插件尚未更改。')
    }
}
$versionMatch = [regex]::Match([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'mori_krita\version.py')), '(?m)^\s*VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$')
if (-not $versionMatch.Success) { throw '插件版本文件无效。请重新完整解压安装包，原插件尚未更改。' }
$pluginVersion = $versionMatch.Groups[1].Value
. (Join-Path $PSScriptRoot 'installer-support.ps1')
if ($CheckOnly) {
    $version = Get-MoriWebViewVersion
    if (-not $version) { throw '未检测到 WebView2 Runtime；正常运行安装插件.cmd时会使用包内组件自动安装。' }
    Write-Output ('安装包插件版本：' + $pluginVersion)
    Write-Output ('组件检查通过：WebView2 Runtime ' + $version)
    return
}
$runtime = Ensure-MoriWebViewRuntime -PackageRoot $PSScriptRoot
$resourceRoot = [System.IO.Path]::GetFullPath($ResourceDirectory)
$pluginRoot = Join-Path $resourceRoot 'pykrita'
$destination = [System.IO.Path]::GetFullPath((Join-Path $pluginRoot 'mori_krita'))
if (-not $destination.StartsWith($resourceRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '插件安装路径超出资源目录。'
}
New-Item -ItemType Directory -Path $pluginRoot -Force | Out-Null
if (Test-Path -LiteralPath $destination) {
    $backup = $destination + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    if (-not $backup.StartsWith($resourceRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw '插件备份路径超出资源目录。'
    }
    Move-Item -LiteralPath $destination -Destination $backup
    Write-Output "已备份旧插件：$backup"
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'mori_krita') -File |
    Where-Object { $_.Extension -in '.py', '.html' } |
    Copy-Item -Destination $destination
foreach ($folder in @('web', 'webview-host', 'icons')) {
    $sourceFolder = Join-Path $PSScriptRoot ('mori_krita\' + $folder)
    if (-not (Test-Path -LiteralPath $sourceFolder)) { throw "插件资源缺失：$folder" }
    Copy-Item -LiteralPath $sourceFolder -Destination $destination -Recurse
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'mori_krita.desktop') -Destination $pluginRoot -Force
if (-not $NoEnable) {
    $configPath = Join-Path $env:LOCALAPPDATA 'kritarc'
    $text = if (Test-Path -LiteralPath $configPath) { [System.IO.File]::ReadAllText($configPath) } else { '' }
    $text = [regex]::Replace($text, '(?m)^enable_plugin_mori_krita=[^\r\n]*\r?\n?', '')
    $setting = 'enable_mori_krita=true'
    if ([regex]::IsMatch($text, '(?m)^enable_mori_krita=')) {
        $text = [regex]::Replace($text, '(?m)^enable_mori_krita=[^\r\n]*', $setting)
    } elseif ([regex]::IsMatch($text, '(?m)^\[python\]\r?$')) {
        $text = [regex]::Replace($text, '(?m)^\[python\]\r?$', "[python]`r`n$setting")
    } else {
        $text = $text.TrimEnd() + "`r`n`r`n[python]`r`n$setting`r`n"
    }
    [System.IO.File]::WriteAllText($configPath, $text, (New-Object System.Text.UTF8Encoding($false)))
}
Write-Output ('已安装 Mori ' + $pluginVersion + '：' + $destination)
Write-Output ('打开 Krita，在 设置 → 停靠面板 → Mori 画布 中打开插件，确认标题版本为 ' + $pluginVersion + '。')

