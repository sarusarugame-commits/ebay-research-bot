@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
color 0A
title eBay/Mercari Research Tool

echo =======================================
echo eBay/メルカリ 無在庫転売検知リサーチツール
echo =======================================
echo.

cd /d "G:\マイドライブ\Python_code\eBayリサーチ部隊"
python main.py

echo.
echo =======================================
echo 処理が完了しました。
pause
