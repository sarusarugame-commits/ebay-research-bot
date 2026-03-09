import datetime
import unicodedata
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1pVY19N3zz67yrGgjGaBwCjLMyVYUHDpgphR-uYsH5fs"
SHEET_NAME = "3月 のコピー"
SERVICE_ACCOUNT_FILE = r"G:\マイドライブ\Python_code\eBayリサーチ部隊\elated-graph-393507-ee7f2fb26fd1.json"

TARGET_MARGIN = 0.06

US_SHIPPING_TABLE = {
    500: 2060, 1000: 3221.4, 1500: 3433.3, 2000: 3606.2,
    3000: 4386.2, 4000: 4982.9, 5000: 6047.6, 6000: 6997.9,
    7000: 8045.7, 8000: 8513.7, 9000: 8983, 10000: 11264.5
}
UK_SHIPPING_TABLE = {
    500: 1571, 1000: 2908.1, 1500: 3383.9, 2000: 3802.5,
    3000: 4253.6, 4000: 4699.5, 5000: 5452.2, 6000: 6605.3,
    7000: 7127.9, 8000: 7650.5, 9000: 8174.4, 10000: 10067.2
}

EXCHANGE_RATE_CELL = 'AI2'


def to_half_width(text):
    if text is None: return ""
    return unicodedata.normalize('NFKC', str(text))


def calculate_shipping_cost(weight_g, length_cm, width_cm, height_cm, table):
    """送料計算: max(実重量, 容積重量=縦×横×高さ/5) × 1.3"""
    try:
        vol_weight = float(length_cm) * float(width_cm) * float(height_cm) / 5.0
        billed_weight = max(float(weight_g), vol_weight) * 1.3
        for threshold in sorted(table.keys()):
            if billed_weight <= threshold:
                return table[threshold]
        return table[10000]
    except Exception:
        return table[500]


def calc_kl(h, j):
    """スプシのK/L計算を再現。
    AG列がカテゴリー未登録時に文字列'0'を返すため
    GoogleスプレッドシートのMIN(数値, 文字列)=数値 の挙動でL=I-AHになる。
    """
    i  = h - 0.5
    ah = j - 0.5
    l  = max(0.0, i - ah)          # MIN(I-AH, "0") → I-AH
    adj = max(0.0, h - j - l)
    k  = min(ah + adj, i) * 1.111
    return k, l


def calc_margin_us(h, j, exchange_rate, cost_jpy, shipping_jpy, is_high_tariff):
    """スプシのM列を再現: O/((K+L)*為替)"""
    k, l = calc_kl(h, j)
    tariff_rate = 0.4 if is_high_tariff else 0.2
    profit = (k*0.9 + l) * exchange_rate * 0.8 \
             - k * 0.9 * tariff_rate * exchange_rate \
             - cost_jpy - shipping_jpy
    denom = (k + l) * exchange_rate
    return (profit / denom) if denom else 0.0, profit


def calc_margin_uk(h, j, exchange_rate, cost_jpy, shipping_jpy):
    """スプシのP列を再現: R/(K*為替)"""
    k, _ = calc_kl(h, j)
    profit = k * 0.9 * exchange_rate * 0.8 - cost_jpy - shipping_jpy
    denom = k * exchange_rate
    return (profit / denom) if denom else 0.0, profit


def find_min_hj(exchange_rate, cost_jpy, us_shipping_jpy, uk_shipping_jpy,
                is_high_tariff, h_start, j_start, target=TARGET_MARGIN):
    """H/J交互二分探索: US/UK両方target達成の最低H/Jを求める"""
    h = h_start
    j = j_start
    for _ in range(200):
        # J固定でH二分探索
        lo, hi = j, max(j * 100, 500000)
        for _ in range(80):
            mid = (lo + hi) / 2
            if calc_margin_us(mid, j, exchange_rate, cost_jpy, us_shipping_jpy, is_high_tariff)[0] < target:
                lo = mid
            else:
                hi = mid
        h_new = hi

        # H固定でJ二分探索
        lo, hi = 0.01, h_new
        for _ in range(80):
            mid = (lo + hi) / 2
            if calc_margin_uk(h_new, mid, exchange_rate, cost_jpy, uk_shipping_jpy)[0] < target:
                lo = mid
            else:
                hi = mid
        j_new = hi

        if abs(h_new - h) < 0.001 and abs(j_new - j) < 0.001:
            break
        h, j = h_new, j_new

    # ceil to 2 decimal places to ensure margin is met
    import math
    h = math.ceil(h * 100) / 100
    j = math.ceil(j * 100) / 100
    return h, j


def write_to_sheet(item_data):
    print("\n[*] Google Sheetsへの書き込み処理を開始します...")

    # 認証・接続
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet(SHEET_NAME)
    except Exception as e:
        print(f"    [!] Google Sheets接続失敗: {e}")
        return False

    # 為替レート取得（AI2セル）
    try:
        er_val = ws.acell(EXCHANGE_RATE_CELL).value
        exchange_rate = float(er_val)
        print(f"    [*] 為替レート: {exchange_rate} 円/USD")
    except Exception:
        exchange_rate = 150.0
        print(f"    [!] 為替レート取得失敗。デフォルト {exchange_rate} 円/USD を使用。")

    def mm_to_cm(val):
        try: return round(float(val) / 10.0, 1)
        except: return ""

    def safe_float(val):
        try: return float(val)
        except: return 0.0

    length_cm = mm_to_cm(item_data.get('length', 0))
    width_cm  = mm_to_cm(item_data.get('width', 0))
    height_cm = mm_to_cm(item_data.get('height', 0))
    weight_g  = safe_float(item_data.get('weight', 0))
    cost      = float(item_data['domestic_price'])
    is_high_tariff = item_data.get('is_high_tariff', False)

    us_shipping_jpy = calculate_shipping_cost(weight_g, length_cm or 0, width_cm or 0, height_cm or 0, US_SHIPPING_TABLE)
    uk_shipping_jpy = calculate_shipping_cost(weight_g, length_cm or 0, width_cm or 0, height_cm or 0, UK_SHIPPING_TABLE)
    print(f"    [*] 国際送料試算: US=¥{us_shipping_jpy:,.0f} / UK=¥{uk_shipping_jpy:,.0f}")

    us_top3 = item_data.get('us_top3_prices', [])
    uk_top3 = item_data.get('uk_top3_prices', [])

    h_usd = min(us_top3) if us_top3 else None
    j_usd = min(uk_top3) if uk_top3 else None

    if h_usd is None and j_usd is not None:
        h_usd = j_usd
    if j_usd is None and h_usd is not None:
        j_usd = h_usd
    if h_usd is None and j_usd is None:
        print("    [SKIP] US/UKともに競合データなし。スキップします。")
        return False

    # 利益率試算
    m_us, profit_us = calc_margin_us(h_usd, j_usd, exchange_rate, cost, us_shipping_jpy, is_high_tariff)
    m_uk, profit_uk = calc_margin_uk(h_usd, j_usd, exchange_rate, cost, uk_shipping_jpy)
    print(f"    [試算] H=${h_usd:.2f} J=${j_usd:.2f} | US利益率: {m_us:.1%} | UK利益率: {m_uk:.1%}")

    # Top3上限（3件未満は上限なし）
    h_max = max(us_top3) if len(us_top3) >= 3 else float('inf')
    j_max = max(uk_top3) if len(uk_top3) >= 3 else float('inf')

    # 利益率未達の場合: H/J交互二分探索で最低値を求める
    if m_us < TARGET_MARGIN or m_uk < TARGET_MARGIN:
        h_min, j_min = find_min_hj(
            exchange_rate, cost, us_shipping_jpy, uk_shipping_jpy,
            is_high_tariff, h_usd, j_usd
        )

        us_achievable = (h_min <= h_max)
        uk_achievable = (j_min <= j_max)

        if not us_achievable and not uk_achievable:
            print(f"    [SKIP] US(必要${h_min:.2f}>上限${h_max:.2f})・UK(必要${j_min:.2f}>上限${j_max:.2f})ともに6%達成不可。")
            return False

        if uk_achievable:
            j_usd = max(j_usd, j_min)

        # J確定後、実際のJ値でHを再計算（JがLに影響するため）
        if us_achievable:
            import math
            lo, hi = j_usd, max(j_usd * 100, 500000)
            for _ in range(80):
                mid = (lo + hi) / 2
                if calc_margin_us(mid, j_usd, exchange_rate, cost, us_shipping_jpy, is_high_tariff)[0] < TARGET_MARGIN:
                    lo = mid
                else:
                    hi = mid
            h_usd = math.ceil(hi * 100) / 100

        m_us, profit_us = calc_margin_us(h_usd, j_usd, exchange_rate, cost, us_shipping_jpy, is_high_tariff)
        m_uk, profit_uk = calc_margin_uk(h_usd, j_usd, exchange_rate, cost, uk_shipping_jpy)
        print(f"    [ADJUST] H=${h_usd:.2f}(US最低${h_min:.2f}) J=${j_usd:.2f}(UK最低${j_min:.2f})")
        print(f"    [ADJUST] US利益率: {m_us:.1%} | UK利益率: {m_uk:.1%}")

    if m_us < TARGET_MARGIN and m_uk < TARGET_MARGIN:
        print(f"    [SKIP] US/UKともに利益率{TARGET_MARGIN:.0%}未満。スキップします。")
        return False

    print(f"    [確定] H=${h_usd:.2f} / J=${j_usd:.2f}")
    print(f"    [確定] US利益率: {m_us:.1%} (¥{profit_us:,.0f}) | UK利益率: {m_uk:.1%} (¥{profit_uk:,.0f})")

    # 書き込み先探索（B列が空の最初の行）
    b_col = ws.col_values(2)
    target_row = None
    for i, val in enumerate(b_col[1:], start=2):  # 2行目から
        if not val:
            target_row = i
            break
    if target_row is None:
        target_row = len(b_col) + 1

    today_str = datetime.datetime.now().strftime("%m/%d").lstrip("0").replace("/0", "/")
    operator_info = f"{today_str}池田"

    updates = {
        2:  to_half_width(item_data['product_name']),
        3:  length_cm,
        4:  width_cm,
        5:  height_cm,
        6:  weight_g if weight_g else "",
        7:  cost,
        8:  h_usd,
        10: j_usd,
        21: item_data.get('ebay_url', ''),
        22: item_data.get('source_url', ''),
        24: "高関税" if is_high_tariff else "低関税",
        26: to_half_width(item_data.get('condition', '')),
        30: operator_info,
    }

    cell_updates = []
    for col_idx, val in updates.items():
        cell_updates.append({
            'range': gspread.utils.rowcol_to_a1(target_row, col_idx),
            'values': [[val]]
        })

    try:
        ws.batch_update(cell_updates, value_input_option='USER_ENTERED')
        print(f"    [SUCCESS] {target_row} 行目に書き込み完了。")
        return True
    except Exception as e:
        print(f"    [!] 書き込み失敗: {e}")
        return False
