param([string]$TestRoot = '')

$ErrorActionPreference = 'Stop'

[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop

. (Join-Path $PSScriptRoot 'installer-support.ps1')



function Progress-Mori([int]$Value, [string]$Message) {

    Write-Output ('MORI_PROGRESS|' + $Value + '|' + $Message)

}

function Child-Path([string]$Root, [string]$Relative) {

    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')

    $path = [IO.Path]::GetFullPath((Join-Path $rootFull $Relative))

    if (-not $path.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {

        throw '安装文件路径无效。'

    }

    return $path

}

function Write-Utf8([string]$Path, [string]$Text) {

    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))

}

function Enable-Plugins([string]$Text) {

    foreach ($name in @('mori_krita', 'ai_diffusion', 'vision_tools')) {

        $key = 'enable_' + $name

        $Text = [regex]::Replace($Text, '(?m)^enable_plugin_' + $name + '=[^\r\n]*\r?\n?', '')

        if ([regex]::IsMatch($Text, '(?m)^' + $key + '=')) {

            $Text = [regex]::Replace($Text, '(?m)^' + $key + '=[^\r\n]*', $key + '=true')

        } elseif ([regex]::IsMatch($Text, '(?m)^\[python\]\r?$')) {

            $Text = [regex]::Replace($Text, '(?m)^\[python\]\r?$', "[python]`r`n$key=true")

        } else {

            $Text = $Text.TrimEnd() + "`r`n`r`n[python]`r`n$key=true`r`n"

        }

    }

    return $Text

}



$test = -not [string]::IsNullOrWhiteSpace($TestRoot)

$nativeArchitecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }

if (-not [Environment]::Is64BitOperatingSystem -or $nativeArchitecture -ne 'AMD64') {

    throw '此安装包适用于 Windows x64。'

}

if (-not $test -and (Get-Process -Name krita -ErrorAction SilentlyContinue)) {

    throw '请先退出 Krita，然后重新点击安装。'

}

if (-not $test -and (Get-Process -Name MoriWebHost -ErrorAction SilentlyContinue)) {

    throw 'Mori 画布引擎仍在运行，请退出 Krita 并关闭旧的 Mori 错误窗口后重试。'

}

$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bundle-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json

if ($manifest.product -cne 'MoriKrita' -or $manifest.build -notmatch '^[a-zA-Z0-9.-]+$') {

    throw '安装清单无效。'

}

Progress-Mori 32 '正在校验安装文件'

$index = 0

foreach ($file in $manifest.files) {

    $source = Child-Path $PSScriptRoot $file.path

    if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or

            (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ine $file.sha256) {

        throw ('安装文件校验失败：' + $file.path)

    }

    $index++

    if ($index % 250 -eq 0) { Progress-Mori (32 + [int](13 * $index / $manifest.files.Count)) '正在校验安装文件' }

}

$signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $PSScriptRoot 'Krita/bin/krita.exe')

if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'K Desktop Environment') {

    throw 'Krita 官方数字签名校验失败。'

}

Progress-Mori 46 '正在检查运行组件'

if ($test) {

    $testFull = [IO.Path]::GetFullPath($TestRoot)

    $installRoot = Child-Path $testFull 'Programs/MoriKrita'

    $resourceRoot = Child-Path $testFull 'Roaming/krita'

    $configPath = Child-Path $testFull 'Local/kritarc'

    $shortcutRoot = Child-Path $testFull 'Shortcuts'

    $runtime = [pscustomobject]@{ Version=(Get-MoriWebViewVersion); Installed=$false; TestOnly=$true }

} else {

    $installRoot = Join-Path $env:LOCALAPPDATA 'Programs/MoriKrita'

    $resourceRoot = Join-Path $env:APPDATA 'krita'

    $configPath = Join-Path $env:LOCALAPPDATA 'kritarc'

    $shortcutRoot = [Environment]::GetFolderPath('Programs')

    if (Test-Path -LiteralPath $configPath) {

        $existingConfig = [IO.File]::ReadAllText($configPath)

        $customResource = [regex]::Match($existingConfig, '(?mi)^ResourceDirectory=([^\r\n]+)')

        if ($customResource.Success) {

            $candidate = $customResource.Groups[1].Value.Trim().Replace('\\', '\')

            if (-not [IO.Path]::IsPathRooted($candidate)) { throw 'Krita 自定义资源目录无效。' }

            $resourceRoot = [IO.Path]::GetFullPath($candidate)

        }

    }

    $runtime = Ensure-MoriWebViewRuntime -PackageRoot $PSScriptRoot

}

$appRoot = Child-Path $installRoot ('Krita-' + $manifest.build)

$marker = Child-Path $appRoot '.mori-bundle'

if ((Test-Path -LiteralPath $appRoot) -and

        (-not (Test-Path -LiteralPath $marker) -or [IO.File]::ReadAllText($marker) -cne $manifest.build)) {

    throw '目标目录已存在其他文件，请移走该目录后重试。'

}

$pluginRoot = Join-Path $resourceRoot 'pykrita'

$actionRoot = Join-Path $resourceRoot 'actions'

$rollbackRoot = Join-Path $PSScriptRoot ('rollback-' + [guid]::NewGuid().ToString('N'))

$changes = New-Object 'System.Collections.Generic.List[object]'

$oldConfig = if (Test-Path -LiteralPath $configPath) { [IO.File]::ReadAllBytes($configPath) } else { $null }

$configWritten = $false



function Install-File([string]$Source, [string]$Target, [bool]$Rollback) {

    if ((Test-Path -LiteralPath $Target -PathType Leaf) -and

            (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash -eq

            (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash) { return }

    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Target)) -Force | Out-Null

    if ($Rollback) {

        $saved = $null

        if (Test-Path -LiteralPath $Target -PathType Leaf) {

            New-Item -ItemType Directory -Path $rollbackRoot -Force | Out-Null

            $saved = Join-Path $rollbackRoot ([guid]::NewGuid().ToString('N'))

            Copy-Item -LiteralPath $Target -Destination $saved

        }

        $changes.Add([pscustomobject]@{ Target=$Target; Saved=$saved })

    }

    Copy-Item -LiteralPath $Source -Destination $Target -Force

    if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash -ne

            (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash) { throw '安装后文件校验失败。' }

}



try {

    Progress-Mori 50 '正在安装 Krita'

    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null

    Write-Utf8 $marker $manifest.build

    $count = 0

    foreach ($file in $manifest.files) {

        $relative = [string]$file.path

        $source = Child-Path $PSScriptRoot $relative

        if ($relative.StartsWith('Krita/')) {

            Install-File $source (Child-Path $appRoot $relative.Substring(6)) $false

        } elseif ($relative.StartsWith('plugins/')) {

            Install-File $source (Child-Path $pluginRoot $relative.Substring(8)) $true

        } elseif ($relative.StartsWith('actions/')) {

            Install-File $source (Child-Path $actionRoot $relative.Substring(8)) $true

        } elseif ($relative.StartsWith('licenses/')) {

            Install-File $source (Child-Path $appRoot ('bundle-' + $relative)) $false

        }

        $count++

        if ($count % 200 -eq 0) { Progress-Mori (50 + [int](43 * $count / $manifest.files.Count)) '正在安装 Krita 与插件' }

    }

    # Bytecode from an older native plugin must not survive an equal-size update.

    foreach ($cacheRoot in @((Join-Path $pluginRoot 'mori_krita/__pycache__'),

                            (Join-Path $pluginRoot 'ai_diffusion/__pycache__'),

                            (Join-Path $pluginRoot 'vision_tools/__pycache__'))) {

        if (Test-Path -LiteralPath $cacheRoot) {

            Get-ChildItem -LiteralPath $cacheRoot -File -Filter '*.pyc' | Remove-Item -Force

        }

    }

    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($configPath)) -Force | Out-Null

    $config = if ($null -ne $oldConfig) { [Text.Encoding]::UTF8.GetString($oldConfig) } else { '' }

    Write-Utf8 $configPath (Enable-Plugins $config)

    $configWritten = $true

    Write-Utf8 $marker $manifest.build

    Progress-Mori 95 '正在创建快捷方式'

    New-Item -ItemType Directory -Path $shortcutRoot -Force | Out-Null

    $shell = New-Object -ComObject WScript.Shell

    $shortcutPaths = @((Join-Path $shortcutRoot 'Krita.lnk'))

    if (-not $test) { $shortcutPaths += Join-Path ([Environment]::GetFolderPath('Desktop')) 'Krita.lnk' }

    foreach ($path in $shortcutPaths) {

        $link = $shell.CreateShortcut($path)

        $link.TargetPath = Join-Path $appRoot 'bin/krita.exe'

        $link.WorkingDirectory = Join-Path $appRoot 'bin'

        $link.IconLocation = $link.TargetPath + ',0'

        $link.Description = 'Krita + Mori + AI Diffusion + Vision Tools'

        $link.Save()

    }

    # Remove the shortcut name used by older bundles only when it points to

    # this installation, so an unrelated user shortcut is never touched.

    $legacyShortcutPaths = @((Join-Path $shortcutRoot 'Mori Krita.lnk'))

    if (-not $test) { $legacyShortcutPaths += Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mori Krita.lnk' }

    $targetExe = [IO.Path]::GetFullPath((Join-Path $appRoot 'bin/krita.exe'))

    $legacyInstallRoot = ([IO.Path]::GetFullPath($installRoot)).TrimEnd('\') + '\'

    foreach ($legacyPath in $legacyShortcutPaths) {

        if (-not (Test-Path -LiteralPath $legacyPath -PathType Leaf)) { continue }

        try {

            $legacy = $shell.CreateShortcut($legacyPath)

            $legacyTarget = [IO.Path]::GetFullPath($legacy.TargetPath)

            if ($legacyTarget -ieq $targetExe -or

                    $legacyTarget.StartsWith($legacyInstallRoot, [StringComparison]::OrdinalIgnoreCase)) {

                Remove-Item -LiteralPath $legacyPath -Force

            }

        } catch { }

    }

    $report = [ordered]@{

        installed=$true; build=$manifest.build; versions=$manifest.versions

        appRoot=$appRoot; resourceRoot=$resourceRoot; configPath=$configPath

        filesVerified=$manifest.files.Count; webView=$runtime; testOnly=$test

        selectionOverlayRemoved=[bool]$manifest.selectionOverlayRemoved
        selectionAssistantEnabled=[bool]$manifest.selectionAssistantEnabled
        completedAt=(Get-Date).ToString('o')

    }

    Write-Utf8 (Join-Path $installRoot 'installation.json') ($report | ConvertTo-Json -Depth 8)

    Write-Output ('MORI_RESULT|' + (Join-Path $appRoot 'bin/krita.exe'))

    Progress-Mori 100 '安装完成'

} catch {

    foreach ($change in $changes) {

        if ($null -ne $change.Saved) { Copy-Item -LiteralPath $change.Saved -Destination $change.Target -Force }

        elseif (Test-Path -LiteralPath $change.Target -PathType Leaf) { Remove-Item -LiteralPath $change.Target -Force }

    }

    if ($configWritten) {

        if ($null -ne $oldConfig) { [IO.File]::WriteAllBytes($configPath, $oldConfig) }

        else { Remove-Item -LiteralPath $configPath -Force }

    }

    throw

}

