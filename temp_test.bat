@echo off
setlocal
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
echo =======================================
echo Amazon Spec Extraction Test
echo =======================================
python "g:\マイドライブ\Python_code\eBayリサーチ部隊\test_amazon.py"
echo.
echo =======================================
pause
