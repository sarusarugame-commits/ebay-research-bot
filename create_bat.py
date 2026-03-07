# -*- coding: utf-8 -*-
import os

bat_content = r"""@echo off
G:
cd "G:\マイドライブ\Python_code\eBayリサーチ部隊"
if not exist validate_ebay_search_v3.py (
    echo [!] 実行ファイルが見つかりません。パスを確認してください。
    echo 現在の場所: %cd%
    pause
    exit /b 1
)
python validate_ebay_search_v3.py
pause
"""

desktop_path = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'run_ebay_test.bat')

with open(desktop_path, 'w', encoding='shift_jis') as f:
    f.write(bat_content)

print(f"Successfully created: {desktop_path}")
