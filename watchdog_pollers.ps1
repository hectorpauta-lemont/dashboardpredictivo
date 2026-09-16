$root = "C:\eros-allocator"
$logDir = "$root\deploy\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = "$logDir\watchdog.log"

function Write-Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Out-File -FilePath $logFile -Append -Encoding utf8
}

function Test-CommandRunning($needle) {
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$needle*" }
        return ($null -ne $procs)
    } catch {
        Write-Log "ERROR revisando '$needle': $_"
        return $true   # si no se pudo consultar, asumir que si corre para no duplicar procesos
    }
}

function Start-Worker($cmdName) {
    try {
        Start-Process cmd -ArgumentList "/c cd /d $root && venv\Scripts\python.exe manage.py $cmdName --hours 720" -WindowStyle Minimized -ErrorAction Stop
    } catch {
        Write-Log "ERROR lanzando ${cmdName}: $_"
    }
}

$lastRollupDay = ""
$tick = 0

Write-Log "watchdog arrancado (PID $PID)"

try {
    while ($true) {
        try {
            if (-not (Test-CommandRunning "run_allocator")) {
                Write-Log "run_allocator no esta corriendo -- relanzando"
                Start-Worker "run_allocator"
            }

            if (-not (Test-CommandRunning "run_earnings_tracker")) {
                Write-Log "run_earnings_tracker no esta corriendo -- relanzando"
                Start-Worker "run_earnings_tracker"
            }

            # rollup diario: una vez por dia calendario, sin depender del Programador de tareas
            $today = Get-Date -Format "yyyy-MM-dd"
            if ($today -ne $lastRollupDay) {
                Write-Log "corriendo build_dashboard_rollups --days 3"
                Push-Location $root
                $out = & "$root\venv\Scripts\python.exe" manage.py build_dashboard_rollups --days 3 2>&1 | Out-String
                Pop-Location
                Write-Log ("rollup salida: " + ($out.Trim() -replace "`r?`n", " | "))
                $lastRollupDay = $today
            }

            $tick += 1
            if ($tick % 10 -eq 0) { Write-Log "sigo vivo (tick $tick)" }
        } catch {
            Write-Log "ERROR en iteracion del watchdog (sigue corriendo): $_"
        }

        Start-Sleep -Seconds 60
    }
} catch {
    Write-Log "FATAL -- el watchdog se esta cerrando: $_"
} finally {
    Write-Log "watchdog TERMINADO (PID $PID)"
}
