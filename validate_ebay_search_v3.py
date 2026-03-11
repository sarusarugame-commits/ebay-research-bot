import requests
import base64
import json
import time
import os
import sys
import urllib.parse
import re
from bs4 import BeautifulSoup

# パス追加 (clip_judge を読み込むため)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import EBAY_APP_ID, EBAY_CLIENT_SECRET
from clip_judge_client import judge_similarity
from llm_vision_judge import verify_model_match
from ebay_scraper import get_browser_page

# ======================================================================
# ⚙️ 動作モード設定（True/False で切り替え）
# ======================================================================
# True  = クライアント仕様: 常に ebay.com(US) を使い、Ship to だけをUS/UKに切り替える（通貨はUSD）
# False = 本来の仕様: USは ebay.com、UKは ebay.co.uk の現地サイトを使い分ける
USE_STRICT_CLIENT_MODE = True
# ======================================================================

def get_ebay_token():
    if not EBAY_APP_ID or not EBAY_CLIENT_SECRET:
        print("[!] EBAY_APP_ID または EBAY_CLIENT_SECRET が設定されていません。")
        return None
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    auth_str = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64_auth}"
    }
    payload = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    try:
        response = requests.post(url, headers=headers, data=payload)
        if response.status_code == 200:
            return response.json().get("access_token")
        else:
            print(f"[!] トークン取得失敗: {response.text}")
            return None
    except Exception as e:
        print(f"[!] トークン取得例外: {e}")
        return None

def extract_item_id(url):
    """URLからeBayのItem IDを抽出する (タイトル付きURLにも対応)"""
    if not url: return None
    # /itm/123456789 または /itm/item-title/123456789 に対応
    match = re.search(r'/itm/(?:.*?/)?(\d{9,})', url)
    return match.group(1) if match else None

def get_valid_shipping_cost(details):
    """APIの詳細データから、有効な送料を抽出する"""
    ship_options = details.get("shippingOptions", [])
    if not ship_options:
        return None  # 配送オプションなし（配送不可）

    # 鑑定サービス(Authenticity Guarantee)がある場合は優先
    for o in ship_options:
        svc_code = (o.get("shippingServiceCode") or "").lower()
        svc_name = (o.get("localizedShippingServiceName") or o.get("shippingServiceName") or "").lower()
        if "authenticator" in svc_code or "authenticator" in svc_name:
            sc = o.get("shippingCost", {})
            return float(sc.get("value", 0)), sc.get("currency", "USD")

    # shippingCostが含まれる全オプションを対象（タイプ問わず）
    all_opts = [o for o in ship_options if "shippingCost" in o]

    if all_opts:
        all_opts.sort(key=lambda o: float(o.get("shippingCost", {}).get("value", 0)))
        sc = all_opts[0].get("shippingCost", {})
        return float(sc.get("value", 0)), sc.get("currency", "USD")

    # shippingCostキーが無くてもFREEなら0円として通す
    free_opts = [o for o in ship_options if (o.get("shippingCostType") or "").upper() == "FREE"]
    if free_opts:
        return 0.0, "USD"

    return None  # 配送不可

def api_ebay_search(keyword, market_id="EBAY_US", condition="NEW", token=None):
    """
    eBay Browse API を使用して商品候補を取得する (スクレイピングの代わり)
    """
    if not token:
        token = get_ebay_token()
    
    cond_filter = "itemConditions:{NEW}" if condition.upper() == "NEW" else "itemConditions:{USED|VERY_GOOD|GOOD|ACCEPTABLE}"
    
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    
    # 🌟 モードによるマーケットIDの切り替え
    target_marketplace = "EBAY_US" if USE_STRICT_CLIENT_MODE else market_id
    
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": target_marketplace,
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=US,zip=10001" if market_id=="EBAY_US" else "contextualLocation=country=GB,zip=E1 6AN",
        "Content-Type": "application/json"
    }
    params = {
        "q": keyword,
        "filter": cond_filter,
        "sort": "pricePlusShipping", # 送料込み最安値順
        "limit": 60
    }
    
    print(f"    [*] eBay API検索開始 ({market_id}, {condition}): {keyword}")
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"    [!] API検索失敗 ({response.status_code}): {response.text}")
            return []
        
        data = response.json()
        items = data.get("itemSummaries", [])
        
        results = []
        for itm in items:
            img_url = itm.get("image", {}).get("imageUrl")
            if not img_url:
                add_imgs = itm.get("additionalImages", [])
                if add_imgs:
                    img_url = add_imgs[0].get("imageUrl")
            
            results.append({
                "itemId": itm.get("itemId"),
                "title": itm.get("title"),
                "item_url": itm.get("itemWebUrl"),
                "img_url": img_url
            })
        print(f"    [*] API検索完了: {len(results)} 件の候補を取得しました。")
        return results
    except Exception as e:
        print(f"    [!] API検索中に例外: {e}")
        return []

def hybrid_ebay_search(keyword, market_id="EBAY_US", condition="NEW", retry_count=0):
    """
    eBayの検索結果ページをスクレイピングして候補を取得する (ハイブリッド仕様)
    見つからなかった場合はキーワードを後ろから削って再検索するフォールバック機能付き。
    """
    # 🌟 モードによるURLの切り替え
    if USE_STRICT_CLIENT_MODE:
        base_url = "https://www.ebay.com/sch/i.html"
    else:
        base_url = "https://www.ebay.com/sch/i.html" if market_id == "EBAY_US" else "https://www.ebay.co.uk/sch/i.html"
    
    # コンディションコード: 新品: 1000, 中古: 3000
    cond_code = "1000" if condition.upper() == "NEW" else "3000"
    
    params = {
        "_nkw": keyword,
        "LH_ItemCondition": cond_code,
        "_sop": 12, # Best Match (関連度優先)
        "rt": "nc"
    }
    
    # =======================================================
    # 🌟 修正: URLパラメータで強制的に Ship to (配送先) を変更
    # =======================================================
    if market_id == "EBAY_US":
        params["_fcid"] = "1"      # Country ID: 1 = US
        params["_stpos"] = "10001" # Zip code: New York
    else:
        params["_fcid"] = "3"      # Country ID: 3 = UK
        params["_stpos"] = "E1 6AN" # Zip code: London
    # =======================================================
    
    search_url = f"{base_url}?{urllib.parse.urlencode(params)}"
    print(f"    [*] eBayハイブリッド検索(SCR)開始 ({market_id}, {condition}): {search_url}")
    
    browser = get_browser_page()
    if not browser:
        print("    [!] ブラウザが取得できないため、API検索にフォールバックします。")
        return api_ebay_search(keyword, market_id, condition)

    results = []
    try:
        tab = browser.latest_tab
        tab.get(search_url)
        
        # ========================================================
        # 修正：早とちりを防ぎ、遅延レンダリングされた「一部一致結果」を待つ
        # ========================================================
        tab.wait.load_start() # ページのロード開始を待つ
        tab.wait(3)           # JavaScriptによる検索結果の非同期描画を待機
        tab.scroll.down(1000) # 画像を読み込ませるためのスクロール
        tab.wait(2)           
        
        soup = BeautifulSoup(tab.html, 'html.parser')
        
        # =======================================================
        # 修正: 's-item' クラス依存をやめ、確実な '/itm/' リンクベースで探索
        # =======================================================
        itm_links = soup.find_all('a', href=re.compile(r'/itm/'))
        print(f"    [DEBUG] HTMLから '/itm/' リンクを {len(itm_links)} 個検出しました。")
        
        for link_tag in itm_links:
            url = link_tag.get('href', '')
            item_id = extract_item_id(url)
            if not item_id: continue
            
            # リンクの親要素を遡って、商品ブロック全体（コンテナ）を特定する
            container = link_tag
            for _ in range(8):
                container = container.parent
                if not container: break
                # <li> または "item" っぽいクラスを持つ <div> をコンテナとみなす
                if container.name == 'li' or (container.name == 'div' and container.get('class') and any('item' in c.lower() for c in container.get('class'))):
                    break
            
            if not container:
                continue

            # タイトルの取得 (タグ同士がくっつくのを防ぐためスペース区切りで取得)
            title_tag = container.find(['div', 'span', 'h3'], class_=re.compile(r'title', re.I))
            title = title_tag.get_text(" ", strip=True) if title_tag else link_tag.get_text(" ", strip=True)
            
            # 🌟修正: 商品を捨てるのではなく、eBay特有の隠しテキスト（ノイズ）だけを置換して消す
            title = re.sub(
                r'Opens in a new window or tab|Opens in a new window|New Listing'
                r'|新しいウィンドウまたはタブに表示されます|新しいウィンドウまたはタブで開く|新出品',
                '', title, flags=re.IGNORECASE
            ).strip()
            
            # 不要なヘッダーリンクなどを除外
            if "Shop on eBay" in title or not title or len(title) < 5: 
                continue
            
            # 画像の取得
            img_tag = container.find('img')
            img_url = ""
            if img_tag:
                # eBayの遅延読み込み対応 (srcがダミーなら data-src などを拾う)
                img_url = img_tag.get('data-src') or img_tag.get('src') or ""
                if img_url.startswith('data:image') and img_tag.get('data-src'):
                    img_url = img_tag.get('data-src')

            # 重複登録の防止
            if not any(r['itemId'] == item_id for r in results):
                results.append({
                    "itemId": item_id,
                    "title": title,
                    "item_url": url,
                    "img_url": img_url
                })
            
            if len(results) >= 60: break
        # =======================================================
        
        # 🌟 フォールバックロジック (0件だった場合の自動緩和) はそのまま残す
        if len(results) == 0 and retry_count < 3:
            words = keyword.split()
            if len(words) >= 3:
                new_keyword = " ".join(words[:-1]) 
                print(f"    [!] 候補が0件のため、キーワードを緩和して再検索します (リトライ {retry_count+1}/3): {new_keyword}")
                # 自身を再帰呼び出し
                return hybrid_ebay_search(new_keyword, market_id, condition, retry_count + 1)
        
        # ⚠️ デバッグ用: それでも0件の場合は、原因究明のためにHTMLを保存する
        if len(results) == 0:
            with open(f"debug_{market_id}_0_results.html", "w", encoding="utf-8") as f:
                f.write(tab.html)
            print(f"    [DEBUG] 原因究明のため、HTMLを debug_{market_id}_0_results.html に保存しました。")

        print(f"    [*] ハイブリッド検索完了: {len(results)} 件の候補を抽出しました。")
        return results
    except Exception as e:
        print(f"    [!] スクレイピング中にエラーが発生したため、API検索にフォールバックします: {e}")
        return api_ebay_search(keyword, market_id, condition)

def get_item_details(token, item_id, marketplace_id):
    url = f"https://api.ebay.com/buy/browse/v1/item/{item_id}"
    
    # 🌟 モードによるマーケットIDの切り替え
    target_marketplace = "EBAY_US" if USE_STRICT_CLIENT_MODE else marketplace_id
    
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": target_marketplace,
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=US,zip=10001" if marketplace_id=="EBAY_US" else "contextualLocation=country=GB,zip=E1 6AN",
        "X-EBAY-C-CURRENCY": "USD" 
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"      [!] get_item_details API Error: {e}")
    return None

def process_market(token, market_id, query, ref_img, condition, model_number="", exclude_id=None):
    print(f"\n[*] {market_id} ハイブリッド相場調査を開始します (Condition: {condition})...")
    
    # 1. ハイブリッド検索 (スクレイピング + APIフォールバック)
    # scraped_candidates = api_ebay_search(query, market_id, condition=condition, token=token) # API専用をコメントアウト
    scraped_candidates = hybrid_ebay_search(query, market_id, condition=condition)
    if not scraped_candidates:
        print(f"    [-] {market_id}: 有効な候補が見見つかりませんでした。")
        return []
    
    # 画像がない商品の救済措置 (詳細APIで補完)
    for c in scraped_candidates:
        if not c.get("img_url"):
            print(f"    [*] 画像が見見つからないため、詳細APIで画像を取得中... (ID: {c['itemId']})")
            formatted_id = f"v1|{c['itemId']}|0" if "|" not in c['itemId'] else c['itemId']
            details = get_item_details(token, formatted_id, market_id)
            if details:
                image = details.get("image", {})
                c["img_url"] = image.get("imageUrl")
                if not c["img_url"]:
                    add_imgs = details.get("additionalImages", [])
                    if add_imgs:
                        c["img_url"] = add_imgs[0].get("imageUrl")
    
    if not scraped_candidates:
        print(f"    [-] {market_id}: 画像のある候補が1件もありませんでした。")
        return []

    # judge_similarity 用にフォーマット調整
    for c in scraped_candidates:
        c["img_urls"] = [c["img_url"]]

    # 1.5 タイトルフィルター（画像判定前）
    if model_number:
        import re as _re
        VARIANT_PATTERN = _re.compile(
            r'\b(G\d+|Mark\s*[IVX]+|MK\s*[23456]|MK\s*[IVX]+|Ver\.?\s*\d+|第\d+世代|Gen\.?\s*\d+)\b',
            _re.IGNORECASE
        )
        model_tokens = [t for t in model_number.lower().split() if len(t) >= 2]
        allowed_variants = set(v.lower() for v in VARIANT_PATTERN.findall(model_number))

        clean, variant_ng = [], []
        for c in scraped_candidates:
            title_lower = c.get("title", "").lower()
            # 必須トークンが揃っていない → 完全除外
            if not all(t in title_lower for t in model_tokens):
                print(f"    [TITLE SKIP] 型番不一致: {c.get('title','')[:50]}")
                continue
            # 世代違いあり → 隔離
            title_variants = set(v.lower() for v in VARIANT_PATTERN.findall(c.get("title", "")))
            unknown_variants = title_variants - allowed_variants
            if unknown_variants:
                print(f"    [TITLE WARN] 世代違い({', '.join(unknown_variants)}): {c.get('title','')[:50]}")
                variant_ng.append(c)
            else:
                clean.append(c)

        if clean:
            # クリーン候補があれば世代違いを除外
            print(f"    [*] タイトルフィルター: {len(clean)} 件クリーン / {len(variant_ng)} 件世代違いを除外")
            scraped_candidates = clean
        elif variant_ng:
            # クリーン候補ゼロ → 世代違いも通してLLMで判定させる
            print(f"    [!] クリーン候補なし。世代違い {len(variant_ng)} 件をLLM判定に委ねます。")
            scraped_candidates = variant_ng
        else:
            print(f"    [!] タイトルフィルター後に候補なし。元の候補をそのまま使用。")

    # 2. 画像判定 (DINOv2)
    print(f"    [*] {market_id}: {len(scraped_candidates)} 件の候補を画像判定中...")
    judged = judge_similarity(ref_img, scraped_candidates)
    
    # 相対審査: clip_judge側で score=0 に落とされた物を除外（既に動的閾値適用済み）
    # 自分のeBay商品IDを除外
    if exclude_id:
        judged = [m for m in judged if str(m.get("itemId", "")) != str(exclude_id)]
    MIN_SCORE = 70.0  # eBay競合は70%未満を除外（誤検知防止）
    top_matches = [m for m in judged if m.get("score", 0) >= MIN_SCORE]
    if not top_matches:
        # 閾値を下げてリトライ（候補が全滅した場合のみ）
        top_matches = [m for m in judged if m.get("score", 0) > 0]
        if top_matches:
            print(f"    [-] 70%以上の候補なし。最高スコア {max(m['score'] for m in top_matches):.1f}% で緩和適用。")
        else:
            print(f"    [-] 画像判定で合格した候補がありませんでした。")
            return []
    
    # 適合率順にソート (スクレイピング側ですでに最安値順に並んでいるが、念のため類似度も加味)
    top_matches.sort(key=lambda x: x["score"], reverse=True)
    
    # 3. APIで配送可否と送料を厳密にチェック
    final_candidates = []
    print(f"    [*] {market_id}: {len(top_matches)} 件の全候補に対して、配送可否と送料を API で精査中...")
    
    for m in top_matches:
        item_id = m.get("itemId")
        formatted_id = f"v1|{item_id}|0" if "|" not in item_id else item_id
        
        details = get_item_details(token, formatted_id, market_id)
        if not details:
            continue
            
        shipping_info = get_valid_shipping_cost(details)
        if shipping_info is None:
            print(f"      [SKIP] ID: {item_id} - 指定国({market_id})への配送不可！")
            continue
            
        ship_val, ship_currency = shipping_info
        price_val = float(details.get("price", {}).get("value", 0))
        price_currency = details.get("price", {}).get("currency", "USD")
        
        total_usd = price_val + ship_val
        
        print(f"      [OK] ID: {item_id} - 配送可 (本体 {price_val} + 送料 {ship_val} {ship_currency}) -> 合計 ${total_usd:.2f} USD")
        
        m["price"] = price_val
        m["currency"] = price_currency
        m["shipping"] = ship_val
        m["ship_currency"] = ship_currency
        m["total_usd"] = total_usd
        final_candidates.append(m)

    # 配送可能なものの中で合計金額順にソート
    final_candidates.sort(key=lambda x: x["total_usd"])

    # ===== LLM型番一致検証 =====
    if model_number and final_candidates:
        print(f"    [*] {market_id}: LLM型番検証を開始します（型番: {model_number}）...")
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _verify_one(cand):
            cand_img = (cand.get("best_img_url")
                        or next((u for u in cand.get("img_urls", []) if u), None))
            if not cand_img:
                return cand, True, ""
            is_match, condition = verify_model_match(ref_img, cand_img, model_number, condition_text=cand.get("condition", ""))
            return cand, is_match, condition

        verified = []
        with _TPE(max_workers=min(len(final_candidates), 5)) as ex:
            for cand, is_match, _ in ex.map(_verify_one, final_candidates):
                if is_match:
                    verified.append(cand)
                else:
                    print(f"    [LLM REJECT] 型番不一致のため除外: {cand.get('title','')[:50]}")

        final_candidates = verified
    
    return final_candidates[:3]

def run_test():
    QUERY = "CASIO Oceanus OCW-S6000"
    REF_IMG = "https://i.ebayimg.com/images/g/FGcAAeSwD8NppOYW/s-l500.webp"
    CONDITION = "NEW"
    
    token = get_ebay_token()
    if not token: return

    us_top3 = process_market(token, "EBAY_US", QUERY, REF_IMG, CONDITION)
    uk_top3 = process_market(token, "EBAY_GB", QUERY, REF_IMG, CONDITION)

    print("\n" + "="*70)
    print("   eBay Hybrid Global Highest & Best Results")
    print("="*70)
    
    results = {"US": us_top3, "UK": uk_top3}
    for m in ["US", "UK"]:
        print(f"\n[Market: eBay {m}]")
        if not results[m]:
            print("  一致する商品は見つかりませんでした。")
        else:
            for i, res in enumerate(results[m], 1):
                print(f"  Rank {i}: {res['title'][:60]}...")
                print(f"    - 合計価格: ${res['total_usd']:,.2f} USD (本体:{res['price']} {res['currency']} + 送料:{res['shipping']})")
                print(f"    - 適合率:   {res['score']:.1f}%")
                print(f"    - URL:      {res['item_url']}")
    print("="*70)

if __name__ == "__main__":
    run_test()
