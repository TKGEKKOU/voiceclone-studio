$ErrorActionPreference = 'Stop'
$Root = 'E:\VCS'
$Python = 'C:\Python311\python.exe'
$Port = 9090

function Get-ProcessSnapshot {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and (
            ($_.CommandLine -match 'uvicorn\s+app:app' -and $_.CommandLine -match '--port\s+9090' -and $_.CommandLine -match 'Python311\\python\.exe') -or
            $_.CommandLine -match [regex]::Escape("$Root\data\runtime\gpt_sovits\api_v2.py") -or
            $_.CommandLine -match [regex]::Escape("$Root\data\frp\frpc.exe -c $Root\data\frp\frpc.toml")
        )
    })
}

function Get-ManagedProcess([string]$Kind) {
    $items = Get-ProcessSnapshot
    switch ($Kind) {
        'studio' { return @($items | Where-Object { $_.CommandLine -match 'uvicorn\s+app:app' -and $_.CommandLine -match '--port\s+9090' -and $_.CommandLine -match 'Python311\\python\.exe' }) }
        'gpt' { return @($items | Where-Object CommandLine -Match ([regex]::Escape("$Root\data\runtime\gpt_sovits\api_v2.py"))) }
        'frp' { return @($items | Where-Object CommandLine -Match ([regex]::Escape("$Root\data\frp\frpc.exe -c $Root\data\frp\frpc.toml"))) }
    }
}

function Get-PortOwner {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-ManagedStopped([string]$Kind, [int]$Seconds = 15) {
    1..$Seconds | ForEach-Object {
        if (-not (Get-ManagedProcess $Kind)) { return $true }
        Start-Sleep -Seconds 1
    }
    return (-not (Get-ManagedProcess $Kind))
}

function Stop-Managed([string]$Kind, [string]$Label) {
    $processes = Get-ManagedProcess $Kind
    if (-not $processes) {
        Write-Host "$Label：未运行" -ForegroundColor DarkGray
        return $true
    }
    foreach ($process in $processes) {
        Write-Host "$Label：停止 PID $($process.ProcessId)" -ForegroundColor Yellow
        & taskkill.exe /PID $process.ProcessId /T /F *> $null
    }
    if (Wait-ManagedStopped $Kind) {
        Write-Host "$Label：已确认停止" -ForegroundColor Green
        return $true
    }
    Write-Host "$Label：停止超时，未继续关闭后续服务" -ForegroundColor Red
    return $false
}

function Start-Studio {
    $studio = Get-ManagedProcess 'studio'
    if ($studio) {
        Write-Host "Studio 已运行，PID $($studio[0].ProcessId)" -ForegroundColor Green
        return
    }
    $owner = Get-PortOwner
    if ($owner) {
        Write-Host "端口 $Port 已被 PID $($owner[0].OwningProcess) 占用，但不是本 Studio，未启动。" -ForegroundColor Red
        return
    }
    Write-Host "只启动 Studio 主服务，不启动 GPT-SoVITS 或 FRP。" -ForegroundColor Cyan
    Start-Process -FilePath $Python -ArgumentList '-m','uvicorn','app:app','--host','127.0.0.1','--port',$Port -WorkingDirectory $Root -WindowStyle Normal
    1..10 | ForEach-Object {
        Start-Sleep -Seconds 1
        if ((Get-ManagedProcess 'studio') -and (Get-PortOwner)) {
            Write-Host "Studio 已启动，PID $((Get-ManagedProcess 'studio')[0].ProcessId)" -ForegroundColor Green
            return
        }
    }
    Write-Host "Studio 启动后未能确认监听端口，请查看控制台输出。" -ForegroundColor Red
}

function Stop-All {
    Write-Host "按 GPT-SoVITS -> FRP -> Studio 顺序关闭。" -ForegroundColor Cyan
    if (-not (Stop-Managed 'gpt' 'GPT-SoVITS')) { return }
    if (-not (Stop-Managed 'frp' 'FRP')) { return }
    if (-not (Stop-Managed 'studio' 'Studio')) { return }
    if (-not (Get-PortOwner)) {
        Write-Host "端口 $Port 已释放，全部服务确认停止。" -ForegroundColor Green
    } else {
        Write-Host "端口 $Port 仍被占用，可能不是本 Studio 进程。" -ForegroundColor Red
    }
}

if ($args[0] -eq 'stop') {
    Stop-All
} else {
    Start-Studio
}


