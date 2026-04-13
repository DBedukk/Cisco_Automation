@echo off
echo ============================================================
echo  FMC Object Parser - Setup and Run
echo  Requires Python 3.8 or higher
echo ============================================================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python 3.8 or higher and make sure to
    echo check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo Python found:
python --version
echo.

:: Create venv if it doesn't exist
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
    echo Done.
    echo.
)

:: Activate venv
call venv\Scripts\activate.bat

:: Install packages from offline folder
echo Installing required libraries from offline_packages...
pip install --no-index --find-links=offline_packages -r requirements.txt --quiet
echo Done.
echo.

:: Prompt for FMC details
echo ============================================================
echo  Ready to run FMC Object Parser
echo ============================================================
echo.
set /p FMC_IP="Enter FMC IP address: "
set /p FMC_USER="Enter FMC username: "

echo.
python FMC_Object_Parser.py -s %FMC_IP% -u %FMC_USER%

echo.
echo Output saved to the 'output' folder.
pause
