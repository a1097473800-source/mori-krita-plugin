param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\qa-output\updater-0.4.16'))
$ErrorActionPreference = 'Stop'
$script:checks = 0
function Assert-MoriTest {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw ('ASSERT: ' + $Message) }
    $script:checks++
}
function Assert-MoriThrows {
    param([scriptblock]$Action, [string]$Pattern)
    $thrown = $null
    try { & $Action | Out-Null } catch { $thrown = $_.Exception.Message }
    Assert-MoriTest ($null -ne $thrown -and $thrown -like ('*' + $Pattern + '*')) ('Expected error ' + $Pattern + ', got ' + $thrown)
}
# This fixture never queries or closes a running Krita process.
function Get-Process { [CmdletBinding()] param([string]$Name) return @() }
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$output = [IO.Path]::GetFullPath($OutputDirectory)
$work = Join-Path $output ('中文 路径-' + [guid]::NewGuid().ToString('N'))
$package = Join-Path $work '更新 包'
$packagePlugin = Join-Path $package 'mori_krita'
New-Item -ItemType Directory -Path $packagePlugin -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $project 'update-installed.ps1') -Destination $package
Get-ChildItem -LiteralPath (Join-Path $project 'mori_krita') -File |
    Where-Object { $_.Extension -in '.py','.html' } | Copy-Item -Destination $packagePlugin
Copy-Item -LiteralPath (Join-Path $project 'mori_krita\web') -Destination $packagePlugin -Recurse
Copy-Item -LiteralPath (Join-Path $project 'mori_krita\webview-host') -Destination $packagePlugin -Recurse
Copy-Item -LiteralPath (Join-Path $project 'windowlayouts') -Destination $package -Recurse
$update = Join-Path $package 'update-installed.ps1'
$version = [regex]::Match([IO.File]::ReadAllText((Join-Path $packagePlugin 'version.py')), 'VERSION = "([^"]+)"').Groups[1].Value
Assert-MoriTest ($version -eq '0.4.17') 'Expected release fixture'
foreach ($scriptName in @('update-installed.ps1','scripts\package-compat-update.ps1')) {
    $tokens=$null; $errors=$null
    $null=[Management.Automation.Language.Parser]::ParseFile((Join-Path $project $scriptName), [ref]$tokens, [ref]$errors)
    Assert-MoriTest ($errors.Count -eq 0) ('Windows PowerShell parser: ' + $scriptName)
}
$records = @()
foreach ($oldVersion in @('0.3.0','0.3.1','0.3.2','0.3.3','0.3.4','0.3.5','0.3.6','0.3.7','0.4.0','0.4.1','0.4.2','0.4.3','0.4.4','0.4.5','0.4.6','0.4.7','0.4.8','0.4.9','0.4.10','0.4.11','0.4.12','0.4.13','0.4.14','0.4.15','0.4.16','0.4.17')) {
    $resource = Join-Path $work ('资源-' + $oldVersion)
    $destination = Join-Path $resource 'pykrita\mori_krita'
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Get-ChildItem -LiteralPath $packagePlugin -File | Copy-Item -Destination $destination
    [IO.File]::WriteAllText((Join-Path $destination 'version.py'), ('VERSION = "' + $oldVersion + '"'))
    [IO.File]::WriteAllText((Join-Path $destination 'selection_controller.py'), 'events = (QEvent.Wheel, QEvent.NativeGesture)')
    [IO.File]::WriteAllText((Join-Path $destination 'krita_bridge.py'), 'if not node.setPixelData(raw, x, y, width, height): raise Exception()')
    [IO.File]::WriteAllText((Join-Path $destination 'gateway.py'), 'Old gateway does not support registration')
    foreach ($relative in @('web\index.html','webview-host\MoriWebHost.exe','__pycache__\stale.pyc')) {
        $path = Join-Path $destination $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
        [IO.File]::WriteAllText($path, ('Preserve fixture ' + $relative))
    }
    $web = Join-Path $destination 'web\index.html'
    $hostFile = Join-Path $destination 'webview-host\MoriWebHost.exe'
    $webHash = (Get-FileHash -LiteralPath $web).Hash
    $hostHash = (Get-FileHash -LiteralPath $hostFile).Hash
    $userData = Join-Path $resource 'test-account-and-canvas.json'
    [IO.File]::WriteAllText($userData, '{"fixture":"Never overwrite account/canvas data"}')
    $dataHash = (Get-FileHash -LiteralPath $userData).Hash
    $backups = Join-Path $work ('备份-' + $oldVersion)
    $beforeHash = (Get-FileHash -LiteralPath (Join-Path $destination 'version.py')).Hash
    $check = & $update -ResourceDirectory $resource -BackupDirectory $backups -CheckOnly
    Assert-MoriTest ((Get-FileHash -LiteralPath (Join-Path $destination 'version.py')).Hash -eq $beforeHash -and -not (Test-Path -LiteralPath $backups)) 'CheckOnly is read-only'
    Assert-MoriTest (($check -join "`n").Contains($destination)) 'Diagnostic prints exact target path'
    $result = & $update -ResourceDirectory $resource -BackupDirectory $backups
    $backup = @(Get-ChildItem -LiteralPath $backups -Directory)
    Assert-MoriTest ($backup.Count -eq 1) 'One existing-plugin backup'
    $reportFile = Join-Path $backup[0].FullName 'update-report.json'
    $report = Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-MoriTest ($report.After.Version -eq $version -and $report.After.RegistrationSupported -and $report.After.NativeGestureFixed -and $report.After.LegacyPixelWriteFixed -and $report.After.SelectionMappingFixed -and $report.After.SelectionLatencyFixed -and $report.After.StartupPanelFixed -and $report.After.NativeImageWorkflowFixed -and $report.After.WebUpdatesSupported) 'All release capabilities verified'
    Assert-MoriTest ($report.After.RealtimeSupported -and @($report.After.FileSHA256.PSObject.Properties).Count -eq 18) 'Realtime files and all key digests recorded'
    Assert-MoriTest $report.After.SmartToolsCompatible 'Community tool preview/run and studio analysis bridge verified'
    Assert-MoriTest $report.After.AspectRatioPreserved 'Generated results retain aspect ratio and transparent centred padding'
    Assert-MoriTest ($report.After.BrowserShutdownFixed -and $report.After.NativeHost.Verified -and $report.VerifiedHostFiles -eq 6) 'Shutdown-compatible host and every binary verified'
    Assert-MoriTest ((Test-Path -LiteralPath (Join-Path $backup[0].FullName 'cleared-python-cache\stale.pyc')) -and -not (Test-Path -LiteralPath (Join-Path $destination '__pycache__'))) 'Old cache preserved in backup and cleared from target'
    Assert-MoriTest ((Get-FileHash -LiteralPath (Join-Path $backup[0].FullName 'replaced-web\index.html')).Hash -eq $webHash -and (Get-FileHash -LiteralPath (Join-Path $backup[0].FullName 'replaced-host\MoriWebHost.exe')).Hash -eq $hostHash) 'Old web and host preserved in backup'
    foreach ($source in Get-ChildItem -LiteralPath (Join-Path $packagePlugin 'webview-host') -File) {
        Assert-MoriTest ((Get-FileHash -LiteralPath $source.FullName).Hash -eq (Get-FileHash -LiteralPath (Join-Path (Join-Path $destination 'webview-host') $source.Name)).Hash) ('Copied host file ' + $source.Name)
    }
    foreach ($source in Get-ChildItem -LiteralPath (Join-Path $packagePlugin 'web') -File -Recurse) {
        $relative = $source.FullName.Substring((Join-Path $packagePlugin 'web').Length + 1)
        Assert-MoriTest ((Get-FileHash -LiteralPath $source.FullName).Hash -eq (Get-FileHash -LiteralPath (Join-Path (Join-Path $destination 'web') $relative)).Hash) ('Copied web file ' + $relative)
    }
    Assert-MoriTest ($report.VerifiedWebFiles -gt 1 -and [bool]$report.WebVersion) 'Web version and verified files recorded'
    Assert-MoriTest ((Get-FileHash -LiteralPath $userData).Hash -eq $dataHash) 'Account/canvas data unchanged'
    foreach ($source in Get-ChildItem -LiteralPath $packagePlugin -File) {
        Assert-MoriTest ((Get-FileHash -LiteralPath $source.FullName).Hash -eq (Get-FileHash -LiteralPath (Join-Path $destination $source.Name)).Hash) ('Copied file ' + $source.Name)
    }
    $records += [ordered]@{from=$oldVersion;to=$version;report=$reportFile;files=$report.VerifiedFiles}
}
# Reject unsupported versions and incomplete registration fixes before making a backup.
$resource = Join-Path $work '资源-0.3.0'
$destination = Join-Path $resource 'pykrita\mori_krita'
$versionFile = Join-Path $destination 'version.py'
[IO.File]::WriteAllText($versionFile, 'VERSION = "0.2.3"')
$rejectBackup = Join-Path $work '拒绝时不备份'
Assert-MoriThrows { & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup } '其他版本请使用完整安装包'
Assert-MoriTest (-not (Test-Path -LiteralPath $rejectBackup)) 'Unsupported target untouched'
[IO.File]::WriteAllText($versionFile, 'VERSION = "0.3.2"')
$gatewayFile = Join-Path $packagePlugin 'gateway.py'
$validGateway = [IO.File]::ReadAllText($gatewayFile)
[IO.File]::WriteAllText($gatewayFile, $validGateway.Replace('auth/(?:session|login|logout|register)', 'auth/(?:session|login|logout)'))
Assert-MoriThrows { & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup } '没有包含完整注册与兼容修复'
Assert-MoriTest (-not (Test-Path -LiteralPath $rejectBackup)) 'Bad package rejected before backup'
[IO.File]::WriteAllText($gatewayFile, $validGateway)
$hostManifest = Join-Path $packagePlugin 'webview-host\host-release.json'
$validHostManifest = [IO.File]::ReadAllText($hostManifest)
$oldHostManifest = $validHostManifest | ConvertFrom-Json
$oldHostManifest.shutdownProtocol = 1
[IO.File]::WriteAllText($hostManifest, ($oldHostManifest | ConvertTo-Json -Depth 8))
Assert-MoriThrows { & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup } '画布引擎更新文件缺失或校验失败'
Assert-MoriTest (-not (Test-Path -LiteralPath $rejectBackup)) 'Old shutdown host rejected before writes'
[IO.File]::WriteAllText($hostManifest, $validHostManifest)
$hostBinary = Join-Path $packagePlugin 'webview-host\MoriWebHost.exe'
$hostBytes = [IO.File]::ReadAllBytes($hostBinary)
[IO.File]::WriteAllText($hostBinary, 'corrupt binary fixture')
Assert-MoriThrows { & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup } '画布引擎更新文件缺失或校验失败'
Assert-MoriTest (-not (Test-Path -LiteralPath $rejectBackup)) 'Corrupt host rejected before writes'
[IO.File]::WriteAllBytes($hostBinary, $hostBytes)
[IO.File]::WriteAllText((Join-Path $packagePlugin 'version.py'), 'VERSION = "9.9.9"')
Assert-MoriThrows { & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup } '更新包版本与更新器不匹配'
Assert-MoriTest (-not (Test-Path -LiteralPath $rejectBackup)) 'Unexpected package version rejected before backup'
[IO.File]::WriteAllText((Join-Path $packagePlugin 'version.py'), ('VERSION = "' + $version + '"'))
# While Krita is running a check remains possible, but writes must stop immediately.
function Get-Process { [CmdletBinding()] param([string]$Name) return [pscustomobject]@{Id=999999;Name='krita'} }
$beforeHash = (Get-FileHash -LiteralPath $versionFile).Hash
$check = & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup -CheckOnly
Assert-MoriTest (($check -join "`n").Contains('本次只读检查没有更新文件')) 'Running Krita permits read-only diagnostic'
Assert-MoriThrows { & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup } 'Krita仍在运行'
Assert-MoriTest ((Get-FileHash -LiteralPath $versionFile).Hash -eq $beforeHash -and -not (Test-Path -LiteralPath $rejectBackup)) 'Running Krita prevents writes'
function Get-Process { [CmdletBinding()] param([string]$Name) if ($Name -eq 'MoriWebHost') { return [pscustomobject]@{Id=999998;Name='MoriWebHost'} } return @() }
Assert-MoriThrows { & $update -ResourceDirectory $resource -BackupDirectory $rejectBackup } 'Mori画布引擎仍在运行'
Assert-MoriTest ((Get-FileHash -LiteralPath $versionFile).Hash -eq $beforeHash -and -not (Test-Path -LiteralPath $rejectBackup)) 'Running host prevents writes'
$summary = [ordered]@{passed=$true;assertions=$script:checks;windows_powershell=$PSVersionTable.PSVersion.ToString();updates=$records;real_plugin_or_user_data_modified=$false;real_processes_started_or_stopped=$false}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $output 'updater-qa.json') -Encoding UTF8
$summary | ConvertTo-Json -Depth 5
