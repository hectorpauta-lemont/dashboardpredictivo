param([switch]$ManualStart)

# Explicit opt-in: old Startup launchers must not restart the collectors.
if (-not $ManualStart) {
    Write-Output "Inicio automatico desactivado. Para iniciar manualmente: .\watchdog_pollers.ps1 -ManualStart"
    exit 0
}

$root = $PSScriptRoot
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
        Start-Process -FilePath "$root\venv\Scripts\python.exe" -ArgumentList "manage.py $cmdName --hours 720" -WorkingDirectory $root -WindowStyle Hidden -ErrorAction Stop
    } catch {
        Write-Log "ERROR lanzando ${cmdName}: $_"
    }
}

$lastRollupDay = ""
$lastResolveAt = Get-Date "2000-01-01"
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

                # sync de precio/tiempo por quota (survey_partner) -- aparte de
                # los loops en vivo a proposito (pedido del usuario,
                # 2026-09-21): no depende de que el proceso este arriba en el
                # instante exacto en que una encuesta cambia de estado, se
                # pone al dia solo una vez al dia y reintenta lo que falto.
                Write-Log "corriendo sync_survey_partner_data --limit 300"
                Push-Location $root
                $out2 = & "$root\venv\Scripts\python.exe" manage.py sync_survey_partner_data --limit 300 2>&1 | Out-String
                Pop-Location
                Write-Log ("sync survey_partner salida: " + ($out2.Trim() -replace "`r?`n", " | "))

                $lastRollupDay = $today
            }

            # reintento de allocations pendientes: cada 3 dias, no diario --
            # pedido explicito del usuario (2026-09-21). Corrige encuestas
            # donde Link ya resolvio el proveedor despues del poll original.
            if (((Get-Date) - $lastResolveAt).TotalDays -ge 3) {
                Write-Log "corriendo resolve_pending_allocations --days 30"
                Push-Location $root
                $out3 = & "$root\venv\Scripts\python.exe" manage.py resolve_pending_allocations --days 30 2>&1 | Out-String
                Pop-Location
                Write-Log ("resolve_pending_allocations salida: " + ($out3.Trim() -replace "`r?`n", " | "))
                $lastResolveAt = Get-Date
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
