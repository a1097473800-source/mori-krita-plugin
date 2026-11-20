param(
    [Parameter(Mandatory=$true)][string]$Setup,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$ExpectedVersion = ''
)
$ErrorActionPreference = 'Stop'
if (-not $ExpectedVersion) {
    $versionFile = Join-Path $PSScriptRoot '..\mori_krita\version.py'
    $versionMatch = [regex]::Match([IO.File]::ReadAllText($versionFile), '(?m)^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"\s*$')
    if (-not $versionMatch.Success) { throw 'Expected release version could not be read.' }
    $ExpectedVersion = $versionMatch.Groups[1].Value
}
$qa = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath (Join-Path $qa 'payload')) { throw 'Use a fresh QA directory.' }
New-Item -ItemType Directory -Path $qa -Force | Out-Null
$profile = Join-Path $qa 'profile'
$resources = Join-Path $profile 'Roaming\krita'
$mori = Join-Path $resources 'pykrita\mori_krita'
New-Item -ItemType Directory -Path (Join-Path $mori '__pycache__'),(Join-Path $profile 'Local') -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $mori 'version.py'), 'VERSION = "0.4.6"')
[IO.File]::WriteAllText((Join-Path $mori '__pycache__\stale.pyc'), 'stale bytecode fixture')
$userData = Join-Path $resources 'fixture-account-and-canvas.json'
[IO.File]::WriteAllText($userData, '{"fixture":"keep existing account and canvas"}')
$userHash = (Get-FileHash -LiteralPath $userData).Hash
$configPath = Join-Path $profile 'Local\kritarc'
[IO.File]::WriteAllText($configPath, "[General]`r`nFixtureSetting=keep`r`n[python]`r`nenable_other_plugin=true`r`nenable_mori_krita=false`r`n")
$process = Start-Process -FilePath ([IO.Path]::GetFullPath($Setup)) -ArgumentList @('--self-test',('"'+$qa+'"')) -WindowStyle Hidden -PassThru
$null = $process.Handle
if (-not $process.WaitForExit(180000)) { throw ('Installer QA timed out; see ' + $qa) }
$process.WaitForExit()
if ($process.ExitCode -ne 0) {
    $errorPath = Join-Path $qa 'setup-test-error.txt'
    if (Test-Path -LiteralPath $errorPath) { Get-Content -LiteralPath $errorPath }
    throw ('Installer self-test failed with exit ' + $process.ExitCode)
}
$script:checks = 0
function Assert-Full([bool]$Condition,[string]$Message) {
    if (-not $Condition) { throw ('Full installer: ' + $Message) }
    $script:checks++
}
$manifest = Get-Content -LiteralPath (Join-Path $qa 'payload\bundle-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$recordPath = Join-Path $profile 'Programs\MoriKrita\installation.json'
$record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Full ($record.installed -and $record.testOnly) 'Installed into the isolated test profile'
Assert-Full ($record.versions.mori -eq $ExpectedVersion -and $manifest.versions.mori -eq $ExpectedVersion) 'Expected Mori version installed'
Assert-Full ($record.filesVerified -eq $manifest.files.Count) 'Every manifest entry verified by installer'
Assert-Full ((Get-FileHash -LiteralPath $userData).Hash -eq $userHash) 'Existing user data preserved'
$config = [IO.File]::ReadAllText($configPath)
Assert-Full ($config.Contains('FixtureSetting=keep') -and $config.Contains('enable_other_plugin=true')) 'Existing preferences and other plugins preserved'
foreach ($name in @('mori_krita','ai_diffusion','vision_tools')) {
    Assert-Full ($config.Contains(('enable_'+$name+'=true'))) ('Plugin enabled: ' + $name)
}
Assert-Full (-not (Test-Path -LiteralPath (Join-Path $mori '__pycache__\stale.pyc'))) 'Old bytecode removed'
Assert-Full ((Get-AuthenticodeSignature -LiteralPath (Join-Path $record.appRoot 'bin\krita.exe')).Status -eq 'Valid') 'Installed Krita signature remains valid'
Assert-Full (Test-Path -LiteralPath (Join-Path $record.appRoot 'share\krita\windowlayouts\Mori 工作台.kwl')) 'Mori workspace available'
$installedFiles = 0
foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    $target = $null
    if ($relative.StartsWith('Krita/')) { $target = Join-Path $record.appRoot $relative.Substring(6) }
    elseif ($relative.StartsWith('plugins/')) { $target = Join-Path (Join-Path $resources 'pykrita') $relative.Substring(8) }
    elseif ($relative.StartsWith('actions/')) { $target = Join-Path (Join-Path $resources 'actions') $relative.Substring(8) }
    elseif ($relative.StartsWith('licenses/')) { $target = Join-Path $record.appRoot ('bundle-' + $relative) }
    if ($null -ne $target) {
        Assert-Full ((Get-FileHash -LiteralPath $target).Hash -ieq $entry.sha256) ('Installed bytes: ' + $relative)
        $installedFiles++
    }
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $profile 'Shortcuts\Krita.lnk'))
Assert-Full ($shortcut.TargetPath -ieq (Join-Path $record.appRoot 'bin\krita.exe')) 'Shortcut launches the new bundled Krita'
$summary = [ordered]@{
    passed=$true; assertions=$script:checks; installedFilesVerified=$installedFiles
    setupSelfTest=$true; versions=$record.versions; build=$record.build
    existingUserDataPreserved=$true; oldCacheRemoved=$true
    pluginsEnabled=@('mori_krita','ai_diffusion','vision_tools')
    installedKritaSignature='Valid'; realUserProfileModified=$false
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qa 'summary.json') -Encoding UTF8
$summary | ConvertTo-Json -Depth 5
