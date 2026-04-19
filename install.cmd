@echo off
setlocal

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 plugins\eqemu-oracle\scripts\eqemu_oracle.py install
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python plugins\eqemu-oracle\scripts\eqemu_oracle.py install
  exit /b %errorlevel%
)

echo Python 3 was not found. Install Python 3 and rerun install.cmd.
exit /b 1
