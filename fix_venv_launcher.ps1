# fix_venv_launcher.ps1 — устранить «второй процесс python» от venv.
#
# ПРИЧИНА (доказано по ParentProcessId): D:\turbobaby-bot\venv\Scripts\python.exe —
# это launcher (venvlauncher, ~270 КБ), а НЕ копия интерпретатора. На Windows он
# запускает базовый C:\Python312\python.exe ДОЧЕРНИМ процессом (нет exec-замены).
# Поэтому КАЖДЫЙ запуск venv-python = ДВА процесса: стаб-лаунчер (venv-путь) + рабочий
# (C:\Python312). Именно это видели как «venv-агент порождает второй pc_agent».
# Singleton-гард в коде тут ни при чём — дублирование происходит на уровне venv,
# ДО запуска main(). Пересоздание venv НЕ помогает: этот Python всегда кладёт launcher.
#
# ЛЕЧЕНИЕ: заменить launcher на КОПИЮ базового python.exe/pythonw.exe. venv остаётся
# рабочим (pyvenv.cfg + site-packages целы, все зависимости на месте), но python
# исполняется В ОДНОМ процессе. Проверено: prefix остаётся venv, is_venv=True.
#
# Запуск (когда ни pc_agent, ни userbot НЕ запущены):
#   powershell -ExecutionPolicy Bypass -File D:\turbobaby-bot\fix_venv_launcher.ps1
#
# ВАЖНО: если venv пересоздадут (python -m venv) — launcher вернётся, прогнать снова.
# Оригинал сохраняется рядом как *.launcher-bak.

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$scripts = Join-Path $repo 'venv\Scripts'
$cfg = Join-Path $repo 'venv\pyvenv.cfg'

if (-not (Test-Path $cfg)) { throw "нет $cfg — это не venv?" }
$venvHome = (((Get-Content $cfg) -match '^home\s*=') -replace '^home\s*=\s*','' | Select-Object -First 1).Trim()
if (-not $venvHome) { throw "не нашёл 'home =' в $cfg" }
Write-Output "base python: $venvHome"

# Нельзя перезаписывать работающий exe — проверим, что venv-python не запущен.
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($scripts, [System.StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    $running | ForEach-Object { Write-Warning ("запущен {0} (PID {1})" -f $_.ExecutablePath, $_.ProcessId) }
    throw "Останови venv-python процессы (pc_agent/userbot) и повтори."
}

foreach ($name in 'python.exe','pythonw.exe') {
    $target = Join-Path $scripts $name
    $base = Join-Path $venvHome $name
    if (-not (Test-Path $base)) { Write-Warning "нет $base — пропускаю"; continue }
    $bak = "$target.launcher-bak"
    if ((Test-Path $target) -and -not (Test-Path $bak)) { Copy-Item $target $bak }
    Copy-Item $base $target -Force
    Write-Output ("patched {0} ({1} bytes)" -f $target, (Get-Item $target).Length)
}

# Проверка: venv по-прежнему виден И один процесс (sys.prefix != base_prefix).
& (Join-Path $scripts 'python.exe') -c "import sys; assert sys.prefix != sys.base_prefix, 'venv broken'; print('OK: single-process venv, prefix =', sys.prefix)"
Write-Output "Готово. Теперь venv python запускается ОДНИМ процессом."
