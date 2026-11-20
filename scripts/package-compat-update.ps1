param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$versionMatch = [regex]::Match([IO.File]::ReadAllText((Join-Path $projectRoot 'mori_krita\version.py')), '(?m)^\s*VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$')
if (-not $versionMatch.Success) { throw '插件版本文件无效，未生成安装包。' }
$packageVersion = $versionMatch.Groups[1].Value
$releaseRoot = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $projectRoot 'releases' }
$stage = Join-Path $releaseRoot ('Mori-Krita-' + $packageVersion + '-Compatibility-Update-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path (Join-Path $stage 'mori_krita') -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'mori_krita') -File |
    Where-Object { $_.Extension -in '.py','.html' } |
    Copy-Item -Destination (Join-Path $stage 'mori_krita')
Copy-Item -LiteralPath (Join-Path $projectRoot 'mori_krita\web') -Destination (Join-Path $stage 'mori_krita') -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'mori_krita\webview-host') -Destination (Join-Path $stage 'mori_krita') -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'windowlayouts') -Destination $stage -Recurse
foreach ($file in @('update-installed.ps1','更新已安装插件.cmd','检查安装.cmd','THIRD-PARTY.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $stage
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'licenses') -Destination $stage -Recurse
$readme = @'
Mori for Krita {VERSION} — 已安装用户小更新

适用于已安装 Mori 0.3.0–{VERSION} 的 Windows 电脑。

安装：
1. 保存作品并完全退出 Krita。
2. 完整解压本 ZIP，双击“更新已安装插件.cmd”。
3. 重开 Krita，将鼠标移到 MORI 标题上，确认版本为 {VERSION}。

本次更新：
面板顶部新增“更新”入口，实时模式和工作台共用更新页。
启动后后台检查插件和网页版本，有更新时入口常驻显示“有更新”。
更新页提供当前版本、最新版本、更新说明及下载按钮；检查失败会显示原因。
修复网页更新兼容判断，保留云端同步刷新及保存冲突处理。
支持关闭自动检查，随时手动检查；原生插件更新需完全退出 Krita 后安装。

暂停只停止后续生成，已经提交的任务会继续完成。
本包沿用兼容的 WebView2 宿主 0.4.8；宿主版本与插件版本分别校验。
更新器会备份原插件、清理旧 Python 缓存并核对替换后的文件。
账号、画布与已安装的微软 WebView2 运行时保留。
“检查安装.cmd”只读检查实际路径与磁盘版本，Krita 运行时也可执行。

默认资源目录：%APPDATA%\krita。自定义目录可在本文件夹运行：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\update-installed.ps1 -ResourceDirectory "你的 Krita 资源目录"

首次安装需先准备完整 Mori/Krita 安装环境，本 ZIP 是已安装用户的更新包。
'@
[System.IO.File]::WriteAllText((Join-Path $stage '更新说明.txt'), $readme.Replace('{VERSION}', $packageVersion), (New-Object System.Text.UTF8Encoding($true)))
$archive = Join-Path $releaseRoot ('Mori-Krita-' + $packageVersion + '-Compatibility-Update.zip')
if (Test-Path -LiteralPath $archive) { $archive = $stage + '.zip' }
Compress-Archive -LiteralPath $stage -DestinationPath $archive
Write-Output $archive
