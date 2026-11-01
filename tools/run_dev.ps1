[CmdletBinding()]
param([switch]$Open,[switch]$Stop)
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$env:HUYAN_NO_REAL='1'
$url='http://localhost:5173'
if($Stop){
    try{
        $source=Invoke-WebRequest "$url/src/components/TextWritingTask.vue?raw" -UseBasicParsing -TimeoutSec 3
        if($source.Content -notmatch 'createDrawingTask'){throw 'Unverified frontend'}
        $ids=@(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 5173 | Select-Object -ExpandProperty OwningProcess -Unique)
        foreach($id in $ids){
            $process=Get-CimInstance Win32_Process -Filter "ProcessId=$id"
            if($process.Name -ne 'node.exe' -or $process.CommandLine -notmatch 'vite[/\\]bin[/\\]vite.js'){throw 'Unverified frontend process'}
            Stop-Process -Id $id -ErrorAction Stop
        }
    }catch{if(@(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 5173).Count){throw}}
    & (Join-Path $PSScriptRoot 'run_backend.ps1') -Stop
    exit $LASTEXITCODE
}
& (Join-Path $PSScriptRoot 'run_backend.ps1') -Ensure -Reload
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
try {
    $client=Invoke-WebRequest "$url/@vite/client" -UseBasicParsing -TimeoutSec 3
    $source=Invoke-WebRequest "$url/src/components/TextWritingTask.vue?raw" -UseBasicParsing -TimeoutSec 3
    if($client.StatusCode -ne 200 -or $source.Content -notmatch 'createDrawingTask'){throw '5173 is not this project'}
    Write-Host "[dev] Reusing $url (hot reload)"
} catch {
    $listeners=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 5173)
    if($listeners.Count){throw '5173 is occupied by an unverified service; no duplicate frontend started.'}
    $node=(Get-Command node.exe -ErrorAction Stop).Source
    $vite=Join-Path $projectRoot 'web/node_modules/vite/bin/vite.js'
    $logRoot=Join-Path $projectRoot 'output/runtime'
    Start-Process -FilePath $node -ArgumentList @('"'+$vite+'"','--host','::','--port','5173','--strictPort') -WorkingDirectory (Join-Path $projectRoot 'web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'vite-dev.stdout.log') -RedirectStandardError (Join-Path $logRoot 'vite-dev.stderr.log') | Out-Null
    $ready=$false
    for($attempt=0;$attempt -lt 30;$attempt++){
        try{if((Invoke-WebRequest "$url/@vite/client" -UseBasicParsing -TimeoutSec 1).StatusCode -eq 200){$ready=$true;break}}catch{}
        Start-Sleep -Milliseconds 200
    }
    if(-not $ready){throw '5173 did not become ready; inspect output/runtime/vite-dev.stderr.log'}
    Write-Host "[dev] Ready $url (hot reload)"
}
if($Open){Start-Process $url}
