@echo off
setlocal enabledelayedexpansion

REM ============================================
REM  AI Platform - Alarm Client - Installer
REM  Opretter en Windows Task Scheduler-opgave der starter checker.js
REM  automatisk og usynligt i baggrunden.
REM
REM  Koer denne fil som Administrator (hoejreklik ->
REM  "Koer som administrator").
REM ============================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [FEJL] Denne fil skal koeres som Administrator.
    echo Hoejreklik install.bat og vaelg "Koer som administrator".
    pause
    exit /b 1
)

set "TASK_NAME=AIPlatformAlarmChecker"
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "VBS_PATH=%SCRIPT_DIR%\run-hidden.vbs"

echo [INFO] Leder efter Node.js...
set "NODE_PATH="
for /f "delims=" %%N in ('where node 2^>nul') do (
    if not defined NODE_PATH set "NODE_PATH=%%N"
)

if not defined NODE_PATH (
    echo [FEJL] Node.js blev ikke fundet i PATH.
    echo Installer Node.js foerst: https://nodejs.org  ^(husk at genstarte terminalen bagefter^)
    pause
    exit /b 1
)
echo [OK] Fandt Node.js: %NODE_PATH%

if not exist "%SCRIPT_DIR%\node_modules" (
    echo [INFO] Installerer npm-afhaengigheder...
    pushd "%SCRIPT_DIR%"
    call npm install
    popd
)

if not exist "%SCRIPT_DIR%\.env" (
    echo [INFO] Opretter .env fra .env.example - ret RAILWAY_URL hvis noedvendigt.
    copy "%SCRIPT_DIR%\.env.example" "%SCRIPT_DIR%\.env" >nul
)

echo [INFO] Skriver skjult-start wrapper ^(%VBS_PATH%^)...
> "%VBS_PATH%" echo Set WshShell = CreateObject("WScript.Shell")
>>"%VBS_PATH%" echo WshShell.CurrentDirectory = "%SCRIPT_DIR%"
>>"%VBS_PATH%" echo q = Chr(34)
>>"%VBS_PATH%" echo cmd = q ^& "%NODE_PATH%" ^& q ^& " checker.js"
>>"%VBS_PATH%" echo WshShell.Run cmd, 0, False

echo [INFO] Opretter/opdaterer Task Scheduler-opgaven "%TASK_NAME%"...
REM Koerer for den bruger der er logget paa (ONLOGON) - noedvendigt fordi en
REM opgave der koerer som SYSTEM ikke kan vise et Chrome-vindue paa skaermen
REM (Session 0-isolation). Saet Windows til automatisk login for at faa en
REM reel "ved boot"-oplevelse, ellers starter checkeren naar brugeren logger paa.
schtasks /create /tn "%TASK_NAME%" ^
    /tr "wscript.exe \"%VBS_PATH%\"" ^
    /sc ONLOGON ^
    /f

if %errorLevel% neq 0 (
    echo [FEJL] Kunne ikke oprette Task Scheduler-opgaven.
    pause
    exit /b 1
)

echo.
echo [OK] "%TASK_NAME%" er installeret og starter checker.js naeste gang du logger paa.
echo.
echo Test den med det samme:   schtasks /run /tn "%TASK_NAME%"
echo Fjern opgaven igen med:   schtasks /delete /tn "%TASK_NAME%" /f
echo.
pause
