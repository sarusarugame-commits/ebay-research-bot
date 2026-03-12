import requests
import json
import time
import re
from config import EBAY_APP_ID, CLIP_JUDGE_SERVER_URL
from clip_judge_client import judge_similarity, judge_similarity_multi
import urllib.parse
from DrissionPage import ChromiumPage, ChromiumOptions

# ----------------------------------------------------------------------
# ⚙️ グローバル設定
# ----------------------------------------------------------------------
# True  = クライアント仕様: 常に ebay.com を使い、Ship to を US/UK に切り替える
# False = 本来の仕様: USは ebay.com、UKは ebay.co.uk を使い分ける
USE_STRICT_CLIENT_MODE = True

# DrissionPage 用のグローバルインスタンス（後で初期化）
_global_browser_page = None

def get_drission_browser():
    """DrissionPageのインスタンスを生成する (headless)"""
    global _global_browser_page
    if _global_browser_page is not None:
        return _global_browser_page
    
    try:
        co = ChromiumOptions()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        # ヘッドレスモードを有効化
        co.headless(True)
        _global_browser_page = ChromiumPage(co)
        return _global_browser_page
    except Exception as e:
        print(f"[!] ブラウザ起動失敗: {e}")
        return None

def handle_ebay_popups(tab):
    """eBay特有のポップアップを閉じる処理"""
    try:
        # GDPR同意ボタンなど
        btn = tab.ele('#gdpr-banner-accept', timeout=1)
        if btn: btn.click()
        btn_close = tab.ele('xpath://button[@aria-label="Close"]', timeout=1)
        if btn_close: btn_close.click()
    except:
        pass

def change_ebay_ship_to(tab, country_name="United States"):
    """
    eBayの 'Ship to' 設定を強制的に変更する。
    country_name: "United States" または "United Kingdom"
    """
    try:
        # 1. 配送先設定ボタンをクリック
        btn = tab.ele('.gh-ship-to__button, #gh-shipto-click', timeout=3)
        if not btn: return
        btn.click()
        time.sleep(1)
        
        # 2. 国の選択ドロップダウン
        # country-selection-selection-id (USなら 223, UKなら 3)
        # またはテキスト検索
        dropdown = tab.ele('.shipto-selection-form', timeout=3)
        if dropdown:
            # 国を選択（DrissionPageのセレクタ機能を使用）
            # もし country_name で直接選べない場合は、IDで分岐する
            country_id = "223" if "States" in country_name else "3"
            tab.ele(f'tag:select').select(country_id)
            time.sleep(0.5)
            # 3. Doneボタン
            done_btn = tab.ele('.shipto-selection-form__submit', timeout=2)
            if done_btn:
                done_btn.click()
                time.sleep(2)
                # print(f"    [*] Ship to を {country_name} に変更しました。")
    except Exception as e:
        # print(f"    [!] Ship to 変更失敗: {e}")
        pass

def get_ebay_token():
    """Client Credentials GrantでeBay APIトークンを取得"""
    from config import EBAY_APP_ID, EBAY_CERT_ID
    if not EBAY_APP_ID or not EBAY_CERT_ID:
        return None
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    import base64
    auth = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {auth}"
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    try:
        r = requests.post(url, headers=headers, data=data, timeout=10)
        return r.json().get("access_token")
    except:
        return None

def hybrid_ebay_search(keyword, market_id="EBAY_US", condition="NEW", retry_count=0, browser=None):
    """
    eBayの検索結果ページをスクレイピングして、
    「送料を含めた合計価格」が取得されたアイテムリストを返す。
    """
    # 指定されたブラウザを使うか、共通のものを取得
    page = browser if browser else get_drission_browser()
    if not page: return []

    # 1. ベースURLの決定
    if USE_STRICT_CLIENT_MODE:
        base_url = "https://www.ebay.com/sch/i.html"
    else:
        base_url = "https://www.ebay.co.uk/sch/i.html" if market_id == "EBAY_GB" else "https://www.ebay.com/sch/i.html"
    
    # 2. パラメータ構築 (新着順: _sop=10, 条件: NEW=1000/USED=3000)
    cond_val = "1000" if condition == "NEW" else "3000"
    params = {
        "_nkw": keyword,
        "_sop": "15", # Lowest Price + Shipping
        "LH_ItemCondition": cond_val,
        "rt": "nc"
    }
    search_url = f"{base_url}?{urllib.parse.urlencode(params)}"
    
    results = []
    try:
        # 3. ページを開く
        page.get(search_url)
        time.sleep(2)
        handle_ebay_popups(page)
        
        # 4. Ship to の変更 (Strictモード時)
        if USE_STRICT_CLIENT_MODE:
            country = "United Kingdom" if market_id == "EBAY_GB" else "United States"
            change_ebay_ship_to(page, country)
        
        # 5. 商品のパース
        # BeautifulSoupを使うために現在のHTMLを取得
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.html, 'html.parser')
        
        for s_item in soup.select('.s-item'):
            if "Shop on eBay" in s_item.get_text(): continue
            
            title_tag = s_item.select_one('.s-item__title')
            link_tag  = s_item.select_one('.s-item__link')
            if not title_tag or not link_tag: continue
            
            title = title_tag.get_text(strip=True)
            url   = link_tag.get('href', '')
            item_id_match = re.search(r'/itm/(\d+)', url)
            item_id = item_id_match.group(1) if item_id_match else ""
            
            # 価格 (本体)
            price_tag = s_item.select_one('.s-item__price')
            price_str = price_tag.get_text(strip=True) if price_tag else "0"
            # 範囲表示 ($10.00 to $12.00) の場合は最小値を取る
            price_val = float(re.sub(r'[^\d.]', '', price_str.split(' to ')[0]) or 0)
            
            # 送料
            shipping_tag = s_item.select_one('.s-item__logistics-cost, .s-item__shipping')
            shipping_str = shipping_tag.get_text(strip=True).lower() if shipping_tag else ""
            ship_val = 0.0
            if "free" in shipping_str:
                ship_val = 0.0
            else:
                m = re.search(r'([\d.,]+)', shipping_str)
                if m:
                    ship_val = float(m.group(1).replace(',', ''))
            
            # 画像
            img_tag = s_item.select_one('.s-item__image-img img')
            img_url = img_tag.get('data-src') or img_tag.get('src') if img_tag else ""
            
            results.append({
                "itemId":   item_id,
                "title":    title,
                "price":    price_val,
                "currency": "GBP" if ("ebay.co.uk" in url and not USE_STRICT_CLIENT_MODE) else "USD",
                "shipping": ship_val,
                "total_usd": price_val + ship_val, # 本来は為替変換が必要だがUSモードならそのまま
                "item_url": url,
                "img_url":  img_url,
                "condition": condition
            })
            if len(results) >= 15: break # 上位15件
            
    except Exception as e:
        print(f"    [!] eBay Scrape エラー ({market_id}): {e}")
        
    # 結果が0件の場合、単語を削って1回だけリトライ
    if not results and retry_count == 0:
        words = keyword.split()
        if len(words) > 1:
            new_query = " ".join(words[:-1])
            # print(f"    [*] 0件のためクエリを短縮して再試行: {new_query}")
            return hybrid_ebay_search(new_query, market_id, condition, retry_count=1, browser=browser)
            
    return results

def process_market(token, market_id, keyword, ref_img_url, condition, model_number=None, exclude_id=None, ebay_title="", base_thresholds=None, browser=None):
    """特定のマーケット（USまたはUK）を調査し、上位3件を返す"""
    label = "US" if market_id == "EBAY_US" else "UK"
    # print(f"  -> {label} 市場の競合を調査中 (Condition: {condition})...")
    
    raw_results = hybrid_ebay_search(keyword, market_id, condition, browser=browser)
    if exclude_id:
        raw_results = [r for r in raw_results if r['itemId'] != exclude_id]

    if not raw_results:
        return []

    # 類似度判定用のペイロード作成
    # CLIP+DINOで一括処理
    judged_list, _ = judge_similarity(ref_img_url, raw_results, base_thresholds=base_thresholds)
    
    # 1. 類似度でフィルタリング (Score > 0 のもの)
    matched = [r for r in judged_list if float(r.get('score', 0)) > 0]
    
    # 2. 型番クロスチェック（タイトルに別の型番が入っていないか）
    # LLMが抽出した型番が含まれているか、または別の型番がタイトルに含まれていないか
    final_candidates = []
    if model_number:
        ref_model = model_number.upper()
        # 数字部分を抽出 (例: F-91WM -> 91)
        # 3桁以上の数字があればそれをメインの型番識別子とする
        ref_digits_m = re.search(r'(\d{3,})', ref_model)
        ref_digits = ref_digits_m.group(1) if ref_digits_m else ""
        
        # ブランドなどのプレフィックス (例: F, MRG, GA)
        ref_prefix_m = re.match(r'^([A-Z]+)', ref_model)
        ref_prefix = ref_prefix_m.group(1) if ref_prefix_m else ""

        for cand in matched:
            cand_title_u = cand['title'].upper()
            
            # --- 強力な除外ロジック ---
            # 1. もしタイトルに別の型番（英字+3桁以上の数字）が入っており、
            #    その「数字部分」が参照型番と一致しない場合は、別モデルとみなして除外する。
            # 例: 参照 1000 -> 候補のタイトルに 1100 があれば除外
            other_models = re.findall(r'[A-Z]{1,4}[\d]{3,}[\w\-]*', cand_title_u)
            is_wrong_model = False
            for tok in other_models:
                tok_digits_m = re.search(r'(\d{3,})', tok)
                tok_digits = tok_digits_m.group(1) if tok_digits_m else ""
                if ref_digits and tok_digits and tok_digits != ref_digits:
                    # 数字が違う型番がタイトルにある
                    is_wrong_model = True
                    break
                
                # 数字が同じでもプレフィックスが違う場合は注意（GA100 vs GD100など）
                tok_prefix_m = re.match(r'^([A-Z]+)', tok)
                tok_prefix = tok_prefix_m.group(1) if tok_prefix_m else ""
                if ref_prefix and tok_prefix and tok_prefix != ref_prefix:
                    # 別のシリーズの同じ数字モデルの可能性が高い
                    is_wrong_model = True
                    break
            
            if is_wrong_model:
                # print(f"    [TITLE_CHECK] 型番不一致の疑いのためスキップ: {cand['title']}")
                continue
                
            final_candidates.append(cand)
    else:
        final_candidates = matched

    # 3. 合計価格でソート
    final_candidates.sort(key=lambda x: x.get('total_usd', float('inf')))
    return final_candidates[:3]

if __name__ == "__main__":
    # 単体テスト用
    token = get_ebay_token()
    kw = "Casio F-91W"
    img = "https://i.ebayimg.com/images/g/q00AAeSwlD9pp6pu/s-l500.webp"
    res = process_market(token, "EBAY_US", kw, img, "NEW", model_number="F-91W")
    for r in res:
        print(f"{r['title']} - ${r['total_usd']}")
