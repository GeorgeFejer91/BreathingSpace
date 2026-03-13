@echo off
REM ============================================
REM PPS Participant Logger Launcher
REM ============================================
REM This batch file launches the Participant Logger GUI
REM It sets the working directory and runs the script

echo Starting Participant Logger...

REM Set Python executable path - UPDATE THIS IF NEEDED
set PYTHON_PATH="C:\Users\cogpsy-vrlab\Anaconda3\python.exe"

REM Set the working directory to the script's location
cd /d "C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\GUI"

REM Verify Python executable exists
IF NOT EXIST %PYTHON_PATH% (
    echo ERROR: Python executable not found at: %PYTHON_PATH%
    echo Please update PYTHON_PATH in this batch file.
    goto error
)

REM Verify the Python script exists
IF NOT EXIST "1. ParticipantLogger.py.py" (
    echo ERROR: Script not found: 1. ParticipantLogger.py.py
    goto error
)

REM Launch the Python script
echo Launching Participant Logger interface...
%PYTHON_PATH% "1. ParticipantLogger.py.py"
IF %ERRORLEVEL% NEQ 0 goto error

echo Participant Logger closed successfully.
goto end

:error
echo.
echo An error occurred while running the Participant Logger.
echo Please check the error messages above.
pause
exit /b 1

:end
exit /b 0
