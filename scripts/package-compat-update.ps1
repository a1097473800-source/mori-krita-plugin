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
修复个人中心「购买兑换码」点击后无反应的问题。
点击购买按钮后，使用系统默认浏览器打开当前配置的店铺地址。
保留 Pinterest 和 API 服务外链支持；本次包含 WebView2 宿主 {VERSION}。

0.4.15 更新：
恢复“通过浏览器登录（支持 Google）”入口，修复旧网页缓存覆盖新版入口的问题。
配套云端浏览器授权服务已经上线，保留原 Google 账号、积分和画布。
授权完成后显示“授权成功”，保留原按钮样式，点击不跳转网站。

修复页面已准备好但仍显示空白的 Windows 进程通信问题。
设置新增“连接与恢复”：刷新页面、重新连接、刷新画面、刷新账号与积分。

进入工作台时显示居中的 Mori SVG 路径描画动画，无加载说明文字。
页面准备完成后才显示浏览器，修复引擎先显示桌面窗口再嵌入引发的黑色闪烁。
切换实时模式或隐藏面板时，原生浏览器窗口同步隐藏；失败时可重试。

此前更新：
支持系统浏览器 Google 登录：点击“通过浏览器登录（支持 Google）”，在网页确认连接后，返回 Krita 继续使用原账号。
插件工具栏、登录和设置窗口使用连写 Mori 标志。
设置页改为黑白灰配色，主要按钮与开关保留紫色，版本号移到左下角。
原图无损默认、实时模式和既有画布同步功能继续保留。
配套 Mori 网站登录接口已经上线；安装本包不会修改云端服务。

暂停只停止后续生成，已经提交的任务会继续完成。
本包使用 WebView2 宿主 {VERSION}；宿主版本与插件版本分别校验。
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
