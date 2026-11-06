# ============================================
# HalkAssets STABLE Build Script
# ============================================

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectDir

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "      HalkAssets STABLE Build" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------
# Проверка файлов
# -------------------------------------------------

if (!(Test-Path ".\main.py")) {
    throw "Не найден main.py"
}

if (!(Test-Path ".\icon.ico")) {
    Write-Host "Внимание: icon.ico не найден. Сборка продолжится без иконки." -ForegroundColor Yellow
    $UseIcon = $false
}
else {
    $UseIcon = $true
}

# -------------------------------------------------
# Очистка
# -------------------------------------------------

Write-Host "[1/6] Очистка старой сборки..."

if (Test-Path ".\build") {
    Remove-Item ".\build" -Recurse -Force
}

if (Test-Path ".\dist") {
    Remove-Item ".\dist" -Recurse -Force
}

if (Test-Path ".\HalkAssets.spec") {
    Remove-Item ".\HalkAssets.spec" -Force
}

if (Test-Path ".\main.spec") {
    Remove-Item ".\main.spec" -Force
}

# -------------------------------------------------
# Сборка
# -------------------------------------------------
#
# ВАЖНО: используем --onedir (папка), а НЕ --onefile.
#
# --onefile при каждом запуске сначала распаковывает себя во
# временную папку (%TEMP%) — если exe лежит на медленной сетевой
# шаре, это распаковывание ~20+ МБ по сети происходит заново
# каждый раз, и программа стартует очень медленно.
#
# --onedir распаковывается один раз при сборке, а запускается сразу
# без промежуточной распаковки — для сетевой версии это работает
# заметно быстрее.

Write-Host "[2/6] Запуск PyInstaller (режим --onedir)..."

# --collect-data reportlab — библиотека печати PDF-акта инвентаризации
# хранит рядом с собой служебные файлы (метрики стандартных шрифтов
# и т.п.), которые PyInstaller сам не находит. Без этого флага exe
# соберётся без ошибок, но упадёт с FileNotFoundError в момент,
# когда пользователь попробует напечатать акт инвентаризации.

if ($UseIcon) {

    pyinstaller `
        --clean `
        --noconfirm `
        --onedir `
        --windowed `
        --name "HalkAssets" `
        --icon ".\icon.ico" `
        --collect-data reportlab `
        ".\main.py"

}
else {

    pyinstaller `
        --clean `
        --noconfirm `
        --onedir `
        --windowed `
        --name "HalkAssets" `
        --collect-data reportlab `
        ".\main.py"

}

if ($LASTEXITCODE -ne 0) {
    throw "Ошибка PyInstaller."
}

# -------------------------------------------------
# Подготовка структуры
# -------------------------------------------------
#
# В режиме --onedir сам exe и все DLL PyInstaller кладёт в
# dist\HalkAssets\, а не прямо в dist\ — поэтому data, backup
# и settings.json тоже должны лежать внутри этой папки, рядом
# с exe (программа ищет их именно рядом с собой).

Write-Host "[3/6] Создание рабочих папок..."

$AppDir = ".\dist\HalkAssets"

New-Item "$AppDir\data" -ItemType Directory -Force | Out-Null
New-Item "$AppDir\backup" -ItemType Directory -Force | Out-Null

# -------------------------------------------------
# Настройки
# -------------------------------------------------

Write-Host "[4/6] Копирование settings.json..."

if (Test-Path ".\settings.json") {
    Copy-Item ".\settings.json" "$AppDir\" -Force
}

# Копируем файл заставки (splash*.png / logo*.png) рядом с EXE.
# Программа сама найдёт его при запуске (см. splash.py).
# Если файла нет — программа запустится без заставки, это не ошибка.
$splashFiles = Get-ChildItem -Path "." -File |
    Where-Object {
        $_.Name -match '^(splash|logo).*\.(png|jpg|jpeg|gif|bmp)$'
    }
foreach ($f in $splashFiles) {
    Copy-Item $f.FullName "$AppDir\" -Force
    Write-Host "   Заставка: $($f.Name)"
}

# -------------------------------------------------
# Чистка неиспользуемых файлов
# -------------------------------------------------
#
# PyInstaller на всякий случай тянет в сборку куда больше, чем
# программе реально нужно (демо-шрифты reportlab, подписи системных
# диалогов Tcl/Tk на полусотне языков). Ничего из этого в работе
# программы не участвует, поэтому спокойно удаляем — на видимость
# папки для пользователей (exe + _internal + data + backup +
# settings.json) это не влияет, но сама папка _internal становится
# заметно легче.
#
# ВАЖНО: базу часовых поясов Tcl (_tcl_data\tzdata) сюда намеренно
# не включаем — Денис решил не трогать её лишний раз перед боевым
# запуском.

Write-Host "[5/6] Чистка неиспользуемых файлов сборки..."

$InternalDir = "$AppDir\_internal"

# reportlab\fonts — демо-шрифты самой библиотеки (DarkGarden, Vera,
# каллиграфический callig15 и т.д.). Программа их не использует —
# акт инвентаризации печатается через Arial из системных шрифтов
# Windows, а не через эти файлы.
if (Test-Path "$InternalDir\reportlab\fonts") {
    Remove-Item "$InternalDir\reportlab\fonts" -Recurse -Force
}

# Подписи кнопок системных диалогов Tcl/Tk на полусотне языков —
# оставляем только русский и английский. Если нужного файла локали
# нет, Tcl/Tk просто показывает английский текст по умолчанию.
foreach ($msgsPath in @("$InternalDir\_tcl_data\msgs", "$InternalDir\_tk_data\msgs")) {
    if (Test-Path $msgsPath) {
        Get-ChildItem $msgsPath -Filter "*.msg" |
            Where-Object { $_.BaseName -notmatch '^(ru|en)' } |
            Remove-Item -Force
    }
}

# -------------------------------------------------
# Готово
# -------------------------------------------------

Write-Host "[6/6] Сборка завершена."

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "         BUILD SUCCESSFUL" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""

Write-Host "Программа собрана в папке:"
Write-Host "   dist\HalkAssets\"
Write-Host ""
Write-Host "Исполняемый файл:"
Write-Host "   dist\HalkAssets\HalkAssets.exe"
Write-Host ""
Write-Host "Для переноса или сетевого использования копируйте/расшаривайте"
Write-Host "всю папку dist\HalkAssets\ целиком, а не только exe-файл."

Write-Host ""
Write-Host "Готово."
