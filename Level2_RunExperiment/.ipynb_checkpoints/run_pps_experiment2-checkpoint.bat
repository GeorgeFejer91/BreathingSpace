@echo off
REM ============================================
REM PPS Experiment Launcher
REM ============================================
REM This batch file launches the PPS Experiment GUI
REM It sets the working directory and ensures all
REM paths are correctly referenced

echo Starting PPS Experiment...

REM Set Python executable path - UPDATE THIS TO YOUR PYTHON LOCATION
set PYTHON_PATH="C:\Users\cogpsy-vrlab\Anaconda3\python.exe"

REM Set the working directory
cd /d "C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment"

REM Verify Python executable exists
IF NOT EXIST %PYTHON_PATH% (
    echo ERROR: Python executable not found at: %PYTHON_PATH%
    echo Please edit this batch file to set the correct Python path
    goto error
)

REM Verify the Python scripts exist
IF NOT EXIST "pps_experiment_GUI.py" (
    echo ERROR: GUI script not found: pps_experiment_GUI.py
    goto error
)

IF NOT EXIST "pps_audio_playback.py" (
    echo ERROR: Audio playback script not found: pps_audio_playback.py
    goto error
)

REM Clean up any previous status files
IF EXIST "experiment_status.json" del "experiment_status.json"

REM Launch the GUI
echo Launching experiment interface...
%PYTHON_PATH% pps_experiment_GUI.py
IF %ERRORLEVEL% NEQ 0 goto error

echo Experiment completed.
goto end

:error
echo.
echo An error occurred while running the experiment.
echo Please check the error messages above.
pause
exit /b 1

:end
exit /b 0