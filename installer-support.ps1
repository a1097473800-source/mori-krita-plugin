# Windows PowerShell 5.1 compatible prerequisite checks. No network requests.
# Official detection locations and silent install command:
# https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution

function ConvertTo-MoriWebViewVersion {
    param($Value)
    $parsed = $null
    if ([version]::TryParse([string]$Value, [ref]$parsed) -and $parsed -gt [version]'0.0.0.0') {
        return $parsed
    }
    return $null
}

function Get-MoriWebViewVersion {
    param([scriptblock]$ReadRegistryVersion)
    $clientKey = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $versions = @()
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryHive]::CurrentUser)) {
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32, [Microsoft.Win32.RegistryView]::Registry64)) {
            $baseKey = $null
            $key = $null
            try {
                if ($ReadRegistryVersion) {
                    $value = & $ReadRegistryVersion $hive $view $clientKey
                } else {
                    $baseKey = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
                    $key = $baseKey.OpenSubKey($clientKey)
                    $value = if ($key) { $key.GetValue('pv', $null) } else { $null }
                }
                $version = ConvertTo-MoriWebViewVersion $value
                if ($null -ne $version) { $versions += $version }
            } catch [System.Security.SecurityException] {
                # Another readable user/machine location can still identify it.
            } catch [System.UnauthorizedAccessException] {
            } finally {
                if ($key) { $key.Dispose() }
                if ($baseKey) { $baseKey.Dispose() }
            }
        }
    }
    if ($versions.Count) { return ($versions | Sort-Object -Descending | Select-Object -First 1).ToString() }
    return $null
}

function Test-MoriMicrosoftSignature {
    param([string]$Path, [string]$Publisher)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    return ($signature.Status -eq [System.Management.Automation.SignatureStatus]::Valid `
        -and $null -ne $signature.SignerCertificate `
        -and $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -ceq $Publisher)
}

function Ensure-MoriWebViewRuntime {
    param(
        [Parameter(Mandatory=$true)][string]$PackageRoot,
        [scriptblock]$DetectVersion = { Get-MoriWebViewVersion },
        [scriptblock]$CheckSignature = { param($path, $publisher) Test-MoriMicrosoftSignature $path $publisher },
        [scriptblock]$RunInstaller = {
            param($path)
            $process = Start-Process -FilePath $path -ArgumentList @('/silent', '/install') -WindowStyle Hidden -PassThru -Wait
            return $process.ExitCode
        }
    )
    $version = ConvertTo-MoriWebViewVersion (& $DetectVersion)
    if ($null -ne $version) {
        Write-Host ('已检测到 WebView2 Runtime ' + $version + '，直接使用。')
        return [pscustomobject]@{ Version=$version.ToString(); Installed=$false; ExitCode=$null }
    }

    $prerequisiteRoot = Join-Path $PackageRoot 'prerequisites'
    $manifestPath = Join-Path $prerequisiteRoot 'manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw '离线组件清单缺失，请重新完整解压安装包。原插件尚未更改。'
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw '离线组件清单无法读取，请重新下载安装包。原插件尚未更改。'
    }
    if ($manifest.fileName -cne 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe' `
            -or $manifest.publisher -cne 'Microsoft Corporation' `
            -or [string]$manifest.sha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw '离线组件清单无效，请重新下载安装包。原插件尚未更改。'
    }
    $installer = Join-Path $prerequisiteRoot $manifest.fileName
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw '离线 WebView2 安装程序缺失，请重新完整解压安装包。原插件尚未更改。'
    }
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ine $manifest.sha256) {
        throw '离线组件校验失败，文件可能未复制完整，请重新下载安装包。原插件尚未更改。'
    }
    if (-not (& $CheckSignature $installer $manifest.publisher)) {
        throw '离线组件的 Microsoft 数字签名验证失败，已停止安装。原插件尚未更改。'
    }
    Write-Host '未检测到 WebView2 Runtime，正在使用包内的微软离线安装程序补齐组件。'
    Write-Host '请等待安装完成，无需手动下载其他文件。'
    try {
        $exitCode = & $RunInstaller $installer
    } catch {
        throw '无法启动包内 WebView2 安装程序，请检查系统是否限制程序运行。原插件尚未更改。'
    }
    $after = ConvertTo-MoriWebViewVersion (& $DetectVersion)
    if ($exitCode -notin @(0, 3010)) {
        throw ('WebView2 组件安装未完成（退出码 ' + $exitCode + '）。请重试安装或检查系统安装限制。原插件尚未更改。')
    }
    if ($null -eq $after) {
        if ($exitCode -eq 3010) {
            throw '组件安装要求重新启动Windows；重启后请再次运行安装插件.cmd。原插件尚未更改。'
        }
        throw '组件安装程序已结束，但未检测到可用的 WebView2 Runtime，请重试安装。原插件尚未更改。'
    }
    Write-Host ('WebView2 Runtime ' + $after + ' 已就绪。')
    return [pscustomobject]@{ Version=$after.ToString(); Installed=$true; ExitCode=$exitCode }
}
