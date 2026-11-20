param(
    [string]$ResourceDirectory = (Join-Path $env:APPDATA 'krita'),
    [string]$BackupDirectory = (Join-Path $env:LOCALAPPDATA 'Mori\KritaPlugin\backups'),
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
if (-not $CheckOnly -and (Get-Process -Name krita -ErrorAction SilentlyContinue)) {
    throw '尚未更新，不会生效：Krita仍在运行。请保存作品并完全退出所有Krita窗口，再运行兼容更新。'
}
if (-not $CheckOnly -and (Get-Process -Name MoriWebHost -ErrorAction SilentlyContinue)) {
    throw '尚未更新：Mori画布引擎仍在运行。请确认Krita已完全退出，并关闭旧的Mori退出错误窗口后重试。'
}
$resourceRoot = [System.IO.Path]::GetFullPath($ResourceDirectory)
$destination = [System.IO.Path]::GetFullPath((Join-Path $resourceRoot 'pykrita\mori_krita'))
if (-not $destination.StartsWith($resourceRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '插件路径超出指定资源目录。'
}
$versionPattern = '(?m)^\s*VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$'
# Only the listed releases share the frontend/runtime contract of this small update.
$supportedInstalledVersions = @('0.3.0','0.3.1','0.3.2','0.3.3','0.3.4','0.3.5','0.3.6','0.3.7','0.4.0','0.4.1','0.4.2','0.4.3','0.4.4','0.4.5','0.4.6','0.4.7','0.4.8','0.4.9','0.4.10')
$sourceRoot = Join-Path $PSScriptRoot 'mori_krita'
$layoutSource = Join-Path $PSScriptRoot 'windowlayouts\Mori 工作台.kwl'
$layoutDestination = Join-Path $resourceRoot 'windowlayouts\Mori 工作台.kwl'
$sourceVersionFile = Join-Path $sourceRoot 'version.py'
if (-not (Test-Path -LiteralPath $sourceVersionFile -PathType Leaf)) {
    throw '更新文件缺失：version.py。请完整解压更新包。'
}
if (-not (Test-Path -LiteralPath $layoutSource -PathType Leaf)) {
    throw '更新文件缺失：windowlayouts\Mori 工作台.kwl。请完整解压同一个发布包。'
}
$sourceVersionMatch = [regex]::Match([System.IO.File]::ReadAllText($sourceVersionFile), $versionPattern)
if (-not $sourceVersionMatch.Success -or $sourceVersionMatch.Groups[1].Value -ne $supportedInstalledVersions[-1]) {
    throw '更新包版本与更新器不匹配，请重新完整解压同一个发布包。'
}
$packageVersion = $sourceVersionMatch.Groups[1].Value
function Get-MoriHostReport {
    param([string]$Directory)
    $hostRoot = Join-Path $Directory 'webview-host'
    $report = [ordered]@{Verified=$false;Version=$null;ShutdownProtocol=0;Files=0;ExecutableSHA256=$null}
    try {
        $manifest = Get-Content -LiteralPath (Join-Path $hostRoot 'host-release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $expected = @('MoriWebHost.exe','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','WebView2-LICENSE.txt')
        if ($manifest.schemaVersion -ne 1 -or $manifest.hostVersion -notmatch '^\d+\.\d+\.\d+$' -or $manifest.shutdownProtocol -ne 2 -or @($manifest.files).Count -ne $expected.Count) { return [pscustomobject]$report }
        $names = @($manifest.files | ForEach-Object { $_.name })
        if (@($names | Select-Object -Unique).Count -ne $expected.Count -or @(Compare-Object $expected $names).Count -ne 0) { return [pscustomobject]$report }
        foreach ($entry in $manifest.files) {
            $file = Join-Path $hostRoot $entry.name
            if ($entry.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or $entry.size -le 0 -or (Get-Item -LiteralPath $file).Length -ne $entry.size -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ine $entry.sha256) { return [pscustomobject]$report }
        }
        $report.Verified=$true
        $report.Version=$manifest.hostVersion
        $report.ShutdownProtocol=$manifest.shutdownProtocol
        $report.Files=$expected.Count
        $report.ExecutableSHA256=(Get-FileHash -LiteralPath (Join-Path $hostRoot 'MoriWebHost.exe') -Algorithm SHA256).Hash
    } catch { }
    return [pscustomobject]$report
}
function Get-MoriUpdateReport {
    param([string]$Directory)
    $versionFile = Join-Path $Directory 'version.py'
    $controllerFile = Join-Path $Directory 'selection_controller.py'
    $bridgeFile = Join-Path $Directory 'krita_bridge.py'
    $gatewayFile = Join-Path $Directory 'gateway.py'
    $apiFile = Join-Path $Directory 'api.py'
    $mappingFile = Join-Path $Directory 'selection_edit.py'
    $geometryFile = Join-Path $Directory 'selection_geometry.py'
    $startupFile = Join-Path $Directory '__init__.py'
    $dockerFile = Join-Path $Directory 'docker.py'
    $composerFile = Join-Path $Directory 'selection_composer.py'
    $tasksFile = Join-Path $Directory 'selection_tasks.py'
    $version = '未找到'
    $controller = ''
    $bridge = ''
    $gateway = ''
    $api = ''
    $mapping = ''
    $geometry = ''
    $startup = ''
    $docker = ''
    $composer = ''
    $tasks = ''
    $hash = $null
    $hashes = [ordered]@{}
    if (Test-Path -LiteralPath $versionFile -PathType Leaf) {
        $match = [regex]::Match([System.IO.File]::ReadAllText($versionFile), $versionPattern)
        if ($match.Success) { $version = $match.Groups[1].Value }
    }
    if (Test-Path -LiteralPath $controllerFile -PathType Leaf) {
        $controller = [System.IO.File]::ReadAllText($controllerFile)
        $hash = (Get-FileHash -LiteralPath $controllerFile -Algorithm SHA256).Hash
    }
    if (Test-Path -LiteralPath $bridgeFile -PathType Leaf) {
        $bridge = [System.IO.File]::ReadAllText($bridgeFile)
    }
    if (Test-Path -LiteralPath $gatewayFile -PathType Leaf) { $gateway = [System.IO.File]::ReadAllText($gatewayFile) }
    if (Test-Path -LiteralPath $apiFile -PathType Leaf) { $api = [System.IO.File]::ReadAllText($apiFile) }
    if (Test-Path -LiteralPath $mappingFile -PathType Leaf) { $mapping = [System.IO.File]::ReadAllText($mappingFile) }
    if (Test-Path -LiteralPath $geometryFile -PathType Leaf) { $geometry = [System.IO.File]::ReadAllText($geometryFile) }
    if (Test-Path -LiteralPath $startupFile -PathType Leaf) { $startup = [System.IO.File]::ReadAllText($startupFile) }
    if (Test-Path -LiteralPath $dockerFile -PathType Leaf) { $docker = [System.IO.File]::ReadAllText($dockerFile) }
    if (Test-Path -LiteralPath $composerFile -PathType Leaf) { $composer = [System.IO.File]::ReadAllText($composerFile) }
    if (Test-Path -LiteralPath $tasksFile -PathType Leaf) { $tasks = [System.IO.File]::ReadAllText($tasksFile) }
    foreach ($name in @('version.py','__init__.py','docker.py','selection_composer.py','selection_controller.py','selection_geometry.py','selection_edit.py','selection_tasks.py','krita_bridge.py','gateway.py','api.py','webview.py','realtime_panel.py','realtime_bridge.py','realtime_task.py','realtime_controller.py','reference_presets.py','login_dialog.py')) {
        $path = Join-Path $Directory $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { $hashes[$name] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
    }
    $registrationFixed = ($gateway.Contains('auth/(?:session|login|logout|register)') `
        -and $gateway.Contains('if pathname == "/api/auth/register" and method != "POST":') `
        -and $gateway.Contains("('/api/auth/session','/api/auth/login','/api/auth/register')") `
        -and $gateway.Contains('("/api/auth/session", "/api/auth/login", "/api/auth/register", "/api/auth/logout")') `
        -and $api.Contains('route in ("/api/auth/login", "/api/auth/register") and method == "POST"'))
    $directGesture = [regex]::IsMatch($controller, '(?m)^\s*(?!#)[^\r\n]*\bQEvent\.NativeGesture\b')
    $nativeHost = Get-MoriHostReport $Directory
    return [pscustomobject][ordered]@{
        Path=$Directory
        Version=$version
        NativeGestureFixed=($controller.Contains('native_gesture = getattr(QEvent, "NativeGesture", None)') -and -not $directGesture)
        LegacyPixelWriteFixed=$bridge.Contains('if written is False:')
        RegistrationSupported=$registrationFixed
        SelectionMappingFixed=($mapping.Contains('def prepare_selection_edit(') -and $mapping.Contains('def map_selection_result(') -and $bridge.Contains('selection_mapping') -and $controller.Contains('context_padding=True'))
        SelectionLatencyFixed=($geometry.Contains('class SelectionBusy(') -and $geometry.Contains('tryBarrierLock') -and $controller.Contains('SelectionBusy') -and -not [regex]::IsMatch($controller, '(?m)^\s*application\.installEventFilter\(self\)'))
        StartupPanelFixed=($startup.Contains('class _StartupVisibility(') -and $docker.Contains('setProperty("ShowOnWelcomePage", False)') -and $startup.Contains('if not has_view:'))
        RealtimeSupported=($docker.Contains('RealtimeController(self, self._realtime_panel)') -and (Test-Path -LiteralPath (Join-Path $Directory 'realtime_panel.py')) -and (Test-Path -LiteralPath (Join-Path $Directory 'realtime_bridge.py')) -and (Test-Path -LiteralPath (Join-Path $Directory 'realtime_task.py')) -and (Test-Path -LiteralPath (Join-Path $Directory 'realtime_controller.py')))
        NativeImageWorkflowFixed=($tasks.Contains('def _finish_expected_image(') -and $tasks.Contains('timings_ms') -and $composer.Contains('payload["expect_image"] = True'))
        WebUpdatesSupported=((Test-Path -LiteralPath (Join-Path $Directory 'web_updates.py')) -and $docker.Contains('WebUpdateManager'))
        SmartToolsCompatible=($gateway.Contains('(?:tool-preview|run)') -and $gateway.Contains('/api/tool-library/capabilities') -and $gateway.Contains('(?:cmf-analysis|design-analysis)') -and $gateway.Contains('(?:tool-library/tools|community/items)'))
        BrowserShutdownFixed=($nativeHost.Verified -and $nativeHost.Version -in @('0.4.6','0.4.7','0.4.8','0.4.9'))
        AspectRatioPreserved=($bridge.Contains('def _fit_import_image(') -and $bridge.Contains('scale = min(width / image.width(), height / image.height())') -and $bridge.Contains('image = _fit_import_image(image, target.width, target.height)') -and -not $bridge.Contains('image.scaled(target.width, target.height, Qt.IgnoreAspectRatio'))
        NativeHost=$nativeHost
        FileSHA256=$hashes
        DirectNativeGestureReference=$directGesture
        SelectionControllerSHA256=$hash
        PythonCachePresent=(Test-Path -LiteralPath (Join-Path $Directory '__pycache__') -PathType Container)
        KritaRunning=[bool](Get-Process -Name krita -ErrorAction SilentlyContinue)
    }
}
function Show-MoriUpdateReport {
    param($Report, [string]$Label)
    Write-Output ($Label + '路径：' + $Report.Path)
    Write-Output ($Label + '磁盘版本：' + $Report.Version)
    Write-Output ('NativeGesture兼容修复：' + $Report.NativeGestureFixed)
    Write-Output ('旧版图层写入兼容修复：' + $Report.LegacyPixelWriteFixed)
    Write-Output ('注册接口与登录会话修复：' + $Report.RegistrationSupported)
    Write-Output ('局部修改上下文与选区映射修复：' + $Report.SelectionMappingFixed)
    Write-Output ('框选非阻塞读取与事件监听修复：' + $Report.SelectionLatencyFixed)
    Write-Output ('欢迎页隐藏 / 画布内显示 Mori：' + $Report.StartupPanelFixed)
    Write-Output ('原生实时模式：' + $Report.RealtimeSupported)
    Write-Output ('智能体改图与分段计时：' + $Report.NativeImageWorkflowFixed)
    Write-Output ('网页自动更新：' + $Report.WebUpdatesSupported)
    Write-Output ('社区智能工具与材质分析兼容：' + $Report.SmartToolsCompatible)
    Write-Output ('退出时WebView2安全释放：' + $Report.BrowserShutdownFixed)
    Write-Output ('生成结果等比居中与透明留边：' + $Report.AspectRatioPreserved)
    foreach ($name in $Report.FileSHA256.Keys) { Write-Output ($name + ' SHA256：' + $Report.FileSHA256[$name]) }
}
$before = Get-MoriUpdateReport $destination
Write-Output ('此更新包版本：' + $packageVersion)
Show-MoriUpdateReport $before '当前安装'
if ($CheckOnly) {
    if ($before.Version -eq $packageVersion -and $before.NativeGestureFixed -and $before.LegacyPixelWriteFixed -and $before.RegistrationSupported -and $before.SelectionMappingFixed -and $before.SelectionLatencyFixed -and $before.StartupPanelFixed -and $before.NativeImageWorkflowFixed -and $before.WebUpdatesSupported -and $before.RealtimeSupported -and $before.SmartToolsCompatible -and $before.BrowserShutdownFixed -and $before.AspectRatioPreserved) {
        Write-Output '磁盘文件已包含本次选区映射、注册与兼容修复。若面板标题仍显示旧版本，请保存作品并完全退出所有Krita进程再打开。'
    } else {
        Write-Output '当前路径的磁盘文件尚未完整更新，请解压新版小包并运行“更新已安装插件.cmd”。'
    }
    if ($before.KritaRunning) { Write-Output '检测到Krita仍在运行；本次只读检查没有更新文件。' }
    return
}
foreach ($required in @('version.py','__init__.py','docker.py','web\index.html','webview-host\MoriWebHost.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $destination $required) -PathType Leaf)) {
        throw '未找到已完整安装的Mori插件。这个小包只用于更新；请先使用完整安装包。'
    }
}
$installedVersion = [regex]::Match([System.IO.File]::ReadAllText((Join-Path $destination 'version.py')), $versionPattern)
if (-not $installedVersion.Success -or $installedVersion.Groups[1].Value -notin $supportedInstalledVersions) {
    throw ('这个更新只适用于Mori ' + ($supportedInstalledVersions -join '、') + '。其他版本请使用完整安装包。')
}
$sourceFiles = @(Get-ChildItem -LiteralPath $sourceRoot -File | Where-Object { $_.Extension -in '.py','.html' })
foreach ($required in @('version.py','__init__.py','docker.py','selection_composer.py','selection_controller.py','selection_geometry.py','qt_lifetime.py','krita_bridge.py','selection_edit.py','selection_tasks.py','gateway.py','api.py','web_updates.py','webview.py','realtime_panel.py','realtime_bridge.py','realtime_task.py','realtime_controller.py')) {
    if (-not ($sourceFiles | Where-Object Name -EQ $required)) {
        throw ('兼容更新文件缺失：' + $required + '。原插件尚未更改。')
    }
}
$sourceReport = Get-MoriUpdateReport $sourceRoot
if (-not $sourceReport.NativeGestureFixed -or -not $sourceReport.LegacyPixelWriteFixed -or -not $sourceReport.RegistrationSupported -or -not $sourceReport.SelectionMappingFixed -or -not $sourceReport.SelectionLatencyFixed -or -not $sourceReport.StartupPanelFixed -or -not $sourceReport.NativeImageWorkflowFixed -or -not $sourceReport.WebUpdatesSupported -or -not $sourceReport.RealtimeSupported -or -not $sourceReport.SmartToolsCompatible) {
    throw '这个更新包没有包含完整注册与兼容修复，原插件尚未更改。请使用最新的修复包。'
}
if (-not $sourceReport.AspectRatioPreserved) {
    throw '更新包缺少生成结果等比回填修复，未修改已安装插件。'
}
if (-not $sourceReport.BrowserShutdownFixed) {
    throw '画布引擎更新文件缺失或校验失败，原插件尚未更改。请完整解压同一更新包。'
}
$sourceHost = Join-Path $sourceRoot 'webview-host'
$hostFiles = @(Get-ChildItem -LiteralPath $sourceHost -File)
if ($hostFiles.Count -ne 6 -or @(Get-ChildItem -LiteralPath $sourceHost -Directory).Count -ne 0) {
    throw '画布引擎更新文件清单无效，原插件尚未更改。'
}
$sourceWeb = Join-Path $sourceRoot 'web'
$uiMetadataFile = Join-Path $sourceWeb 'ui-release.json'
if (-not (Test-Path -LiteralPath (Join-Path $sourceWeb 'index.html') -PathType Leaf) -or -not (Test-Path -LiteralPath $uiMetadataFile -PathType Leaf)) {
    throw '网页更新文件缺失，原插件尚未更改。请完整解压同一更新包。'
}
$uiMetadata = Get-Content -LiteralPath $uiMetadataFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($uiMetadata.schemaVersion -ne 1 -or $uiMetadata.uiVersion -notmatch '^[A-Za-z0-9_-]{1,100}$' -or [version]$uiMetadata.bridgeMinVersion -gt [version]$packageVersion) {
    throw '网页版本或桥接要求无效，原插件尚未更改。'
}
$webFiles = @(Get-ChildItem -LiteralPath $sourceWeb -File -Recurse)
$backupRoot = [System.IO.Path]::GetFullPath($BackupDirectory)
$backup = [System.IO.Path]::GetFullPath((Join-Path $backupRoot ('before-compat-' + $packageVersion + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))))
if (-not $backup.StartsWith($backupRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) `
        -or $backup.StartsWith($destination.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '兼容更新备份路径无效，原插件尚未更改。'
}
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
Copy-Item -LiteralPath $destination -Destination $backup -Recurse
Write-Output ('已备份原插件：' + $backup)
$before | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $backup 'before-report.json') -Encoding UTF8
$cache = [System.IO.Path]::GetFullPath((Join-Path $destination '__pycache__'))
$savedCache = [System.IO.Path]::GetFullPath((Join-Path $backup 'cleared-python-cache'))
if (Test-Path -LiteralPath $cache -PathType Container) {
    if (-not $cache.StartsWith($destination.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) `
            -or -not $savedCache.StartsWith($backup.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) `
            -or (Test-Path -LiteralPath $savedCache)) {
        throw ('Python缓存备份路径无效，已保留原版本备份：' + $backup)
    }
    Move-Item -LiteralPath $cache -Destination $savedCache
    Write-Output '已将插件旧Python缓存移入备份。'
}
foreach ($file in $sourceFiles) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $destination $file.Name) -Force
}
foreach ($file in $sourceFiles) {
    if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne `
            (Get-FileHash -LiteralPath (Join-Path $destination $file.Name) -Algorithm SHA256).Hash) {
        throw ('更新文件校验失败：' + $file.Name + '。原版本备份位于：' + $backup)
    }
}
$stagedHost = [IO.Path]::GetFullPath((Join-Path $destination ('.host-update-' + [guid]::NewGuid().ToString('N'))))
$oldHost = [IO.Path]::GetFullPath((Join-Path $destination 'webview-host'))
$savedHost = [IO.Path]::GetFullPath((Join-Path $backup 'replaced-host'))
if (-not $stagedHost.StartsWith($destination.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $oldHost.StartsWith($destination.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $savedHost.StartsWith($backup.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $savedHost)) {
    throw '画布引擎备份路径无效，未替换引擎。'
}
Copy-Item -LiteralPath $sourceHost -Destination $stagedHost -Recurse
foreach ($file in $hostFiles) {
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath (Join-Path $stagedHost $file.Name)).Hash) { throw ('画布引擎暂存校验失败：' + $file.Name) }
}
Move-Item -LiteralPath $oldHost -Destination $savedHost
try { Move-Item -LiteralPath $stagedHost -Destination $oldHost }
catch { Move-Item -LiteralPath $savedHost -Destination $oldHost; throw }
$stagedWeb = [IO.Path]::GetFullPath((Join-Path $destination ('.web-update-' + [guid]::NewGuid().ToString('N'))))
$oldWeb = [IO.Path]::GetFullPath((Join-Path $destination 'web'))
$savedWeb = [IO.Path]::GetFullPath((Join-Path $backup 'replaced-web'))
if (-not $stagedWeb.StartsWith($destination.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $oldWeb.StartsWith($destination.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $savedWeb.StartsWith($backup.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $savedWeb)) {
    throw '网页备份路径无效，未替换网页。'
}
Copy-Item -LiteralPath $sourceWeb -Destination $stagedWeb -Recurse
foreach ($file in $webFiles) {
    $relative = $file.FullName.Substring($sourceWeb.Length + 1)
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath (Join-Path $stagedWeb $relative)).Hash) { throw ('网页文件校验失败：' + $relative) }
}
Move-Item -LiteralPath $oldWeb -Destination $savedWeb
try { Move-Item -LiteralPath $stagedWeb -Destination $oldWeb }
catch { Move-Item -LiteralPath $savedWeb -Destination $oldWeb; throw }
$after = Get-MoriUpdateReport $destination
if ($after.Version -ne $packageVersion -or -not $after.NativeGestureFixed -or -not $after.LegacyPixelWriteFixed -or -not $after.RegistrationSupported -or -not $after.SelectionMappingFixed -or -not $after.SelectionLatencyFixed -or -not $after.StartupPanelFixed -or -not $after.NativeImageWorkflowFixed -or -not $after.WebUpdatesSupported -or -not $after.RealtimeSupported -or -not $after.SmartToolsCompatible `
        -or -not $after.BrowserShutdownFixed -or -not $after.AspectRatioPreserved `
        -or $after.DirectNativeGestureReference) {
    throw ('安装后的兼容标记检查未通过，不能报告更新成功。原版本备份：' + $backup)
}
$layoutBackup = Join-Path $backup 'windowlayouts\Mori 工作台.kwl'
if (Test-Path -LiteralPath $layoutDestination -PathType Leaf) {
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($layoutBackup)) -Force | Out-Null
    Copy-Item -LiteralPath $layoutDestination -Destination $layoutBackup -Force
}
New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($layoutDestination)) -Force | Out-Null
Copy-Item -LiteralPath $layoutSource -Destination $layoutDestination -Force
if ((Get-FileHash -LiteralPath $layoutSource -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $layoutDestination -Algorithm SHA256).Hash) {
    throw ('窗口布局预设写入校验失败。原版本备份位于：' + $backup)
}
$reportFile = Join-Path $backup 'update-report.json'
[ordered]@{Before=$before;After=$after;Backup=$backup;VerifiedFiles=$sourceFiles.Count;VerifiedWebFiles=$webFiles.Count;VerifiedHostFiles=$hostFiles.Count;WebVersion=$uiMetadata.uiVersion;WindowLayout=$layoutDestination;WindowLayoutSHA256=(Get-FileHash -LiteralPath $layoutDestination -Algorithm SHA256).Hash;CompletedAt=(Get-Date -Format 'o')} `
    | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportFile -Encoding UTF8
Show-MoriUpdateReport $after '已更新'
Write-Output ('更新检查报告：' + $reportFile)
if ($after.KritaRunning) { Write-Output '更新过程中检测到Krita已启动：磁盘文件已更新，当前实例必须完全退出后重新打开。' }
Write-Output ('Mori更新已完成。打开Krita后，面板标题应显示 Mori 画布 · ' + $packageVersion + '。')
Write-Output ('网页已同步：' + $uiMetadata.uiVersion + '；后续兼容网页可从网站自动更新。')
Write-Output '本地画布引擎与“Mori 工作台”窗口布局已同步；账号、画布和已安装的微软WebView2运行时保留。'
