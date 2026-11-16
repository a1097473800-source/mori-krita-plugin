param(
    [Parameter(Mandatory=$true)][string]$KritaRunner,
    [string]$Suite = 'run_release_qa',
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\qa-output\native-0.4.7')
)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$output = [IO.Path]::GetFullPath($OutputDirectory)
if ($Suite -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'Invalid QA module name.' }
if (-not (Test-Path -LiteralPath $KritaRunner -PathType Leaf)) { throw 'kritarunner.com was not found.' }
New-Item -ItemType Directory -Path $output -Force | Out-Null
$module = 'mori_release_qa_' + [guid]::NewGuid().ToString('N').Substring(0,12)
$bootstrapRoot = Join-Path $env:APPDATA 'kritarunner'
New-Item -ItemType Directory -Path $bootstrapRoot -Force | Out-Null
$bootstrap = Join-Path $bootstrapRoot ($module + '.py')
$pythonTests = (Join-Path $project 'tests').Replace('\','\\').Replace("'", "\'")
$text = "import sys`nsys.path.insert(0, '" + $pythonTests + "')`nfrom " + $Suite + " import main`n"
[IO.File]::WriteAllText($bootstrap, $text, [Text.UTF8Encoding]::new($false))
$priorPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $bootstrapRoot
    $runner = Start-Process -FilePath ([IO.Path]::GetFullPath($KritaRunner)) -ArgumentList @('--script',$module,'--function','main',('"'+$output+'"')) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $output 'stdout.log') -RedirectStandardError (Join-Path $output 'stderr.log')
    $null = $runner.Handle
    if (-not $runner.WaitForExit(90000)) { throw ('QA did not finish; see ' + $output) }
    $runner.WaitForExit()
    $reportName = if ($Suite -eq 'run_realtime_gui_qa') { 'integration.json' } else { 'summary.json' }
    $reportFile = Join-Path $output $reportName
    if (-not (Test-Path -LiteralPath $reportFile)) { throw ('QA report missing; see ' + $output) }
    $report = Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runner.ExitCode -ne 0 -or -not $report.passed) { throw ('QA failed (exit ' + $runner.ExitCode + '); see ' + $output) }
    Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8
} finally {
    $env:PYTHONPATH = $priorPythonPath
    Remove-Item -LiteralPath $bootstrap -ErrorAction SilentlyContinue
}
