@echo off
setlocal enabledelayedexpansion

title HalkAssets - GitHub upload

cd /d "%~dp0"

REM ============================================================
REM 1. Check environment
REM ============================================================

where git >nul 2>nul
if errorlevel 1 goto NO_GIT

if not exist ".gitignore" goto NO_GITIGNORE
if not exist "main.py" goto NO_MAIN

REM ============================================================
REM 2. Init on first run
REM ============================================================

if not exist ".git" goto DO_INIT
goto SKIP_INIT

:DO_INIT
echo ==========================================
echo   First run - initializing repository
echo ==========================================
git init -b main
if errorlevel 1 goto INIT_FAILED
echo.
goto SKIP_INIT

:INIT_FAILED
echo [ERROR] Failed to init repository.
pause
exit /b 1

:SKIP_INIT

REM ============================================================
REM 3. Remote
REM ============================================================

git remote get-url origin >nul 2>nul
if errorlevel 1 goto ADD_REMOTE

git remote set-url origin https://github.com/filippovsys/HalkAssets.git
goto REMOTE_DONE

:ADD_REMOTE
git remote add origin https://github.com/filippovsys/HalkAssets.git

:REMOTE_DONE

REM ============================================================
REM 4. User name
REM ============================================================

git config user.name >nul 2>nul
if not errorlevel 1 goto NAME_DONE

echo.
set /p GIT_NAME=Enter your name for commits: 
if "!GIT_NAME!"=="" set GIT_NAME=Filippow Denis
git config user.name "!GIT_NAME!"

:NAME_DONE

REM ============================================================
REM 5. User email
REM ============================================================

git config user.email >nul 2>nul
if not errorlevel 1 goto EMAIL_DONE

echo.
set /p GIT_EMAIL=Enter your email: 
if "!GIT_EMAIL!"=="" set GIT_EMAIL=noreply@example.com
git config user.email "!GIT_EMAIL!"

:EMAIL_DONE

REM ============================================================
REM 6. Add files
REM ============================================================

echo.
echo Adding files...
git add .

REM Info: data/database.db is intentionally included
git ls-files --error-unmatch data/database.db >nul 2>nul
if errorlevel 1 goto NO_DB
echo.
echo [INFO] data/database.db will be committed.
:NO_DB

REM ============================================================
REM 7. Commit
REM ============================================================

git diff --cached --quiet
if not errorlevel 1 goto NO_CHANGES

echo.
set /p COMMIT_MSG=Commit message or Enter for update: 
if "!COMMIT_MSG!"=="" set COMMIT_MSG=update
git commit -m "!COMMIT_MSG!"
goto COMMIT_DONE

:NO_CHANGES
echo.
echo No changes to commit.

:COMMIT_DONE

REM ============================================================
REM 8. Push
REM ============================================================

echo.
echo Pushing to GitHub...
git push -u origin main

if errorlevel 1 goto PUSH_FAILED

echo.
echo ==========================================
echo   DONE
echo   https://github.com/filippovsys/HalkAssets
echo ==========================================
echo.
pause
exit /b 0

:PUSH_FAILED
echo.
echo ==========================================
echo   PUSH FAILED
echo ==========================================
echo.
echo Possible reasons:
echo.
echo   1. Not logged in to GitHub. Browser should open on first push.
echo.
echo   2. If console asks for password - use Personal Access Token.
echo      Create here: https://github.com/settings/tokens
echo      Scopes: repo, workflow
echo.
echo   3. If repo already has files - run:
echo      git pull origin main --allow-unrelated-histories
echo      then start this script again.
echo.
pause
exit /b 1

REM ============================================================
REM Error handlers
REM ============================================================

:NO_GIT
echo.
echo [ERROR] Git is not installed.
echo Download: https://git-scm.com/download/win
echo.
pause
exit /b 1

:NO_GITIGNORE
echo.
echo [ERROR] .gitignore not found in project root.
echo.
pause
exit /b 1

:NO_MAIN
echo.
echo [ERROR] main.py not found. Script must be in project root.
echo.
pause
exit /b 1