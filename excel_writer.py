import datetime
import unicodedata
import openpyxl
from openpyxl.styles import Font

# ＝各種設定＝
EXCEL_PATH = r"G:\マイドライブ\Python_code\eBayリサーチ部隊\リサーチシート完全版 のコピー.xlsx"
SHEET_NAME = "3月"

FEE_RATE = 0.28        # Excelシートの関数に合わせた手数料率(28%)
TARGET_MARGIN = 0.06   # 目標利益率(6%)

def to_half_width(text):
    """全角英数字を半角に変換する"""
    if text is None: return ""
    return unicodedata.normalize('NFKC', str(text))

def calculate_min_sell_price(cost_jpy, shipping_jpy, exchange_rate):
    """利益率6%を達成するための最低販売価格(USD)を逆算する"""
    required_jpy = (cost_jpy + shipping_jpy) / (1.0 - FEE_RATE - TARGET_MARGIN)
    return round(required_jpy / exchange_rate, 2)

def adjust_price(top3_prices, min_sell_usd):
    """Top3の中から、最低販売価格を上回っている最安値を返す（事前に安い順に並んでいる前提）"""
    for price in top3_prices:
        if price >= min_sell_usd:
            return price
    return None

def write_to_excel(item_data):
    print("\n[*] Excelシートへの書き込み処理を開始します...")
    
    # 1. Excelファイルの読み込みと為替レート取得
    try:
        wb = openpyxl.load_workbook(EXCEL_PATH)
        ws = wb[SHEET_NAME]
        
        # AI列(35列目)の2行目から為替レートを取得
        exchange_rate = ws.cell(row=2, column=35).value
        if not isinstance(exchange_rate, (int, float)):
            exchange_rate = 150.0 
    except Exception as e:
        print(f"    [!] Excelファイルの読み込みに失敗しました: {e}")
        return False

    # 2. 利益計算シミュレーション
    cost = float(item_data['domestic_price'])
    min_us_usd = calculate_min_sell_price(cost, item_data['us_shipping_jpy'], exchange_rate)
    min_uk_usd = calculate_min_sell_price(cost, item_data['uk_shipping_jpy'], exchange_rate)

    us_top3 = item_data['us_top3_prices']
    uk_top3 = item_data['uk_top3_prices']

    adjusted_us = adjust_price(us_top3, min_us_usd)
    adjusted_uk = adjust_price(uk_top3, min_uk_usd)

    # 両方とも6%未達の場合はスキップ
    if adjusted_us is None and adjusted_uk is None:
        print(f"    [SKIP] US/UKともにTop3内で利益率6%未満のため、データを破棄します。")
        return False

    # 片方だけクリアした場合の強制引き上げ
    if adjusted_us is None:
        adjusted_us = min_us_usd
        print(f"    [ADJUST] USは利益率未達のため、最低価格 ${adjusted_us} に強制調整します。")
    if adjusted_uk is None:
        adjusted_uk = min_uk_usd
        print(f"    [ADJUST] UKは利益率未達のため、最低価格 ${adjusted_uk} に強制調整します。")

    # 3. 書き込み先（B列が空の行）の探索
    target_row = None
    # 既存の最大行か、100行先まで探索して空行を見つける
    max_search_row = ws.max_row + 100
    for row in range(2, max_search_row):
        if not ws.cell(row=row, column=2).value: 
            target_row = row
            break
    if not target_row: target_row = ws.max_row + 1

    # 日付と担当者
    today_str = datetime.datetime.now().strftime("%m/%d").lstrip("0").replace("/0", "/")
    operator_info = f"{today_str}池田"

    # セル更新用のマッピング (列インデックス -> B:2, C:3, D:4, E:5, F:6, G:7, H:8, J:10, V:22, X:24, Z:26, AD:30)
    updates = {
        2: item_data['product_name'],
        3: item_data['length'],
        4: item_data['width'],
        5: item_data['height'],
        6: item_data['weight'],
        7: item_data['domestic_price'],
        8: adjusted_us,
        10: adjusted_uk,
        22: item_data['source_url'],
        24: "高関税" if item_data.get('is_high_tariff') else "低関税",
        26: item_data['condition'],
        30: operator_info
    }

    font_10 = Font(size=10)

    # 4. セルへ書き込み（文字サイズ10適用、背景色維持）
    for col_idx, val in updates.items():
        cell = ws.cell(row=target_row, column=col_idx)
        # 数値の場合は半角変換をスキップ
        if isinstance(val, (int, float)):
            cell.value = val
        else:
            cell.value = to_half_width(val) if val != "" else val
        cell.font = font_10

    # 5. 保存
    try:
        wb.save(EXCEL_PATH)
        print(f"    [SUCCESS] Excelシートの {target_row} 行目に書き込みが完了しました。")
    except PermissionError:
        print(f"    [!] エラー: Excelファイルが開かれています。閉じてから再度実行してください。")
        return False

    return True
