param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\qa\installer-runtime'))
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\installer-support.ps1')
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
function Write-MoriManifest {
    param([string]$Directory, [hashtable]$Overrides=@{})
    $record = @{fileName='MicrosoftEdgeWebView2RuntimeInstallerX64.exe'; publisher='Microsoft Corporation'; sha256=(Get-FileHash -LiteralPath (Join-Path $Directory 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe') -Algorithm SHA256).Hash}
    foreach ($name in $Overrides.Keys) { $record[$name] = $Overrides[$name] }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Directory 'manifest.json') -Encoding UTF8
}

$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$work = Join-Path $output ('Windows 测试路径-' + [guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $work '完整 安装包'
$prerequisites = Join-Path $packageRoot 'prerequisites'
New-Item -ItemType Directory -Path $prerequisites -Force | Out-Null
$installer = Join-Path $prerequisites 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
[System.IO.File]::WriteAllBytes($installer, [Text.Encoding]::UTF8.GetBytes('Offline fixture only; never execute'))
Write-MoriManifest $prerequisites

Assert-MoriTest ((ConvertTo-MoriWebViewVersion '146.0.1.2').ToString() -eq '146.0.1.2') 'Valid runtime version'
foreach ($invalid in @($null, '', '0.0.0.0', 'not-a-version')) {
    Assert-MoriTest ($null -eq (ConvertTo-MoriWebViewVersion $invalid)) 'Missing/invalid registry version is not installed'
}
$locations = New-Object 'System.Collections.Generic.List[string]'
$version = Get-MoriWebViewVersion -ReadRegistryVersion {
    param($hive,$view,$key)
    $locations.Add(($hive.ToString() + '/' + $view.ToString() + '/' + $key))
    if ($hive -eq [Microsoft.Win32.RegistryHive]::LocalMachine -and $view -eq [Microsoft.Win32.RegistryView]::Registry32) { return '145.0.1.0' }
    if ($hive -eq [Microsoft.Win32.RegistryHive]::CurrentUser -and $view -eq [Microsoft.Win32.RegistryView]::Registry64) { return '146.0.2.0' }
    return '0.0.0.0'
}
Assert-MoriTest ($version -eq '146.0.2.0') 'Both machine and per-user installs detected, highest version chosen'
Assert-MoriTest ($locations.Count -eq 4) 'Both registry views checked'
Assert-MoriTest (@($locations | Where-Object { $_ -notlike '*{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' }).Count -eq 0) 'Official Runtime GUID only'
Assert-MoriTest ($null -eq (Get-MoriWebViewVersion -ReadRegistryVersion { return '' })) 'Missing runtime reported'

$result = Ensure-MoriWebViewRuntime -PackageRoot (Join-Path $work '没有安装文件') -DetectVersion { '146.0.1.0' } -CheckSignature { throw 'Signature must not run' } -RunInstaller { throw 'Installer must not run' }
Assert-MoriTest (-not $result.Installed) 'Existing runtime skips installer and signature/file work'

$state = [pscustomobject]@{Reads=0; SignatureChecks=0; Runs=0; Path=$null}
$detect = { $state.Reads++; if ($state.Reads -gt 1) { '146.0.1.0' } }.GetNewClosure()
$signature = { param($path,$publisher) $state.SignatureChecks++; $state.Path=$path; if ($publisher -ne 'Microsoft Corporation') { throw 'Wrong publisher' }; return $true }.GetNewClosure()
$run = { param($path) $state.Runs++; if ($path -ne $state.Path) { throw 'Wrong installer' }; return 0 }.GetNewClosure()
$result = Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion $detect -CheckSignature $signature -RunInstaller $run
Assert-MoriTest ($result.Installed -and $result.Version -eq '146.0.1.0') 'Missing runtime installed and redetected'
Assert-MoriTest ($state.Reads -eq 2 -and $state.Runs -eq 1 -and $state.SignatureChecks -eq 1) 'One signed install followed by mandatory redetection'
Assert-MoriTest ($state.Path -eq $installer) 'Unicode and spaces preserved without shell command construction'

Write-MoriManifest $prerequisites @{sha256=('0'*64)}
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion { $null } -CheckSignature { throw 'Unexpected signature check' } -RunInstaller { throw 'Unexpected process' } } '校验失败'
Write-MoriManifest $prerequisites @{publisher='Other Corporation'}
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion { $null } -RunInstaller { throw 'Unexpected process' } } '清单无效'
Write-MoriManifest $prerequisites @{fileName='..\arbitrary.exe'}
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion { $null } -RunInstaller { throw 'Unexpected process' } } '清单无效'
Write-MoriManifest $prerequisites
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion { $null } -CheckSignature { $false } -RunInstaller { throw 'Unexpected process' } } '数字签名验证失败'
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot (Join-Path $work '没有安装文件') -DetectVersion { $null } -RunInstaller { throw 'Unexpected process' } } '清单缺失'
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion { $null } -CheckSignature { $true } -RunInstaller { 0 } } '未检测到可用'
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion { $null } -CheckSignature { $true } -RunInstaller { 7 } } '退出码 7'
Assert-MoriThrows { Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion { $null } -CheckSignature { $true } -RunInstaller { 3010 } } '重新启动Windows'
$reboot = [pscustomobject]@{Reads=0}
$detectReboot = { $reboot.Reads++; if ($reboot.Reads -gt 1) { '146.0.1.0' } }.GetNewClosure()
$result = Ensure-MoriWebViewRuntime -PackageRoot $packageRoot -DetectVersion $detectReboot -CheckSignature { $true } -RunInstaller { 3010 }
Assert-MoriTest ($result.Installed -and $result.ExitCode -eq 3010) '3010 accepted only when runtime is actually detectable'

# Exercise the real installation script with a missing-component fixture. The
# copied helper's detector alone is replaced; no real installer can be launched.
$required = @('mori_krita.desktop','mori_krita\__init__.py','mori_krita\version.py','mori_krita\web\index.html','mori_krita\webview-host\MoriWebHost.exe','mori_krita\webview-host\WebView2Loader.dll','mori_krita\webview-host\Microsoft.Web.WebView2.Core.dll','mori_krita\webview-host\Microsoft.Web.WebView2.WinForms.dll')
foreach ($relative in $required) {
    $target = Join-Path $packageRoot $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    [IO.File]::WriteAllText($target, 'Fixture')
}
[IO.File]::WriteAllText((Join-Path $packageRoot 'mori_krita\version.py'), 'VERSION = "0.3.3"')
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'mori_krita\icons') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\install.ps1') -Destination $packageRoot
$support = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '..\installer-support.ps1'))
$support += "`r`nfunction Get-MoriWebViewVersion { return `$null }`r`n"
[IO.File]::WriteAllText((Join-Path $packageRoot 'installer-support.ps1'), $support, [Text.UTF8Encoding]::new($true))
$resource = Join-Path $work '旧Krita资源'
$oldPlugin = Join-Path $resource 'pykrita\mori_krita'
New-Item -ItemType Directory -Path $oldPlugin -Force | Out-Null
$marker = Join-Path $oldPlugin 'existing-plugin.txt'
[IO.File]::WriteAllText($marker, 'Existing plugin must remain')
Assert-MoriThrows {
    # Only the copied fixture's process check is mocked. A user's running Krita
    # is never closed or queried for test state, and the unsigned fake installer
    # is rejected before anything can be executed.
    function Get-Process { [CmdletBinding()] param([string]$Name) return @() }
    & (Join-Path $packageRoot 'install.ps1') -ResourceDirectory $resource -NoEnable
} '数字签名验证失败'
Assert-MoriTest (([IO.File]::ReadAllText($marker)) -eq 'Existing plugin must remain') 'Failed prerequisite leaves existing plugin intact'
Assert-MoriTest (@(Get-ChildItem -LiteralPath (Split-Path -Parent $oldPlugin) -Directory -Filter 'mori_krita.backup-*').Count -eq 0) 'Failed prerequisite does not move existing plugin'
$report = [ordered]@{passed=$true;assertions=$script:checks;runtime_installer_executions=0;simulated_installer_executions=1;real_registry_writes=0;existing_plugin_unchanged=$true;windows_powershell=$PSVersionTable.PSVersion.ToString()}
$report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'installer-runtime-qa.json') -Encoding UTF8
$report | ConvertTo-Json
