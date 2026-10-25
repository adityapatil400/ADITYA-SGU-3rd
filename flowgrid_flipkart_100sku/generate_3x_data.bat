@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating Python 3.11 virtual environment...
  py -3.11 -m venv .venv
  if errorlevel 1 goto :error
  .venv\Scripts\python.exe -m pip install --upgrade pip
  if errorlevel 1 goto :error
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto :error
)
echo Generating FlowGrid 100-SKU Flipkart-catalog + synthetic model-ready datasets...
.venv\Scripts\python.exe scripts\generate_flowgrid_demo.py --scale-factor 3 --skus 100 --days 365
if errorlevel 1 goto :error
echo.
echo Validating all CSVs and relationships...
.venv\Scripts\python.exe scripts\validate_dataset.py
if errorlevel 1 goto :error
echo.
echo Dataset generated and validated successfully.
echo To run the forecasting benchmark:
echo .venv\Scripts\python.exe scripts\train_forecasting.py --n-estimators 50 --max-depth 12 --n-jobs 2
pause
exit /b 0
:error
echo.
echo Generation or validation failed. Review the error above.
pause
exit /b 1
