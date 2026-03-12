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
import threading as _threading
_judge_lock = _threading.Lock()

def _judge_similarity_safe(ref_img, candidates, base_thresholds=None, max_retries=3, retry_delay=5):
    """並列スレッド間のmodel_server競合を防ぐシリアライズラッパー（タイムアウト時リトライ付き）"""
    import time as _time
    for attempt in range(1, max_retries + 1):
        try:
            with _judge_lock:
                return judge_similarity(ref_img, candidates, base_thresholds)
        except Exception as e:
            if attempt < max_retries:
                print(f"    [!] judge_similarity 失敗 (試行{attempt}/{max_retries}): {e} -> {retry_delay}秒後にリトライ...")
                _time.sleep(retry_delay)
            else:
                print(f"    [!] judge_similarity 全試行失敗: {e} -> 空リストで続行")
                return [], {}

from llm_vision_judge import verify_model_match

BLUE  = "\033[94m"
RESET = "\033[0m"
def hyperlink(url, text=None):
    label = text if text else (url[:40] + "…" if len(url) > 40 else url)
    return f"\033]8;;{url}\033\\{BLUE}{label}{RESET}\033]8;;\033\\"
from ebay_scraper import get_browser_page, scrape_ebay_item_specs

# ======================================================================
# ⚙️ 動作モード設定（True/False で切り替え）
# ======================================================================
# True  = クライアント仕様: 常に ebay.com(US) を使い、Ship to だけをUS/UKに切り替える（通貨はUSD）
# False = 本来の仕様: USは ebay.com、UKは ebay.co.uk の現地サイトを使い分ける
USE_STRICT_CLIENT_MODE = True

# 商品IDごとのDINOv2スコア・LLM判定結果キャッシュ（US→UK間で再利用）
_item_judge_cache = {}  # {item_id: {"score": float, "best_img_url": str, "llm_match": bool}}
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
        "_sop": 15, # Price + Shipping: lowest first
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

def process_market(token, market_id, keyword, ref_img, condition, exclude_id=None, model_number=None, ebay_title=None, base_thresholds=None):
    """
    指定されたマーケット(US/UK)での検索・スクレイピング・画像判定を一貫して行う
    """
    global _item_judge_cache
    print(f"\n[*] {market_id} ハイブリッド相場調査を開始します (Condition: {condition})...")
    
    # 1. ハイブリッド検索 (スクレイピング + APIフォールバック)
    # scraped_candidates = api_ebay_search(keyword, market_id, condition=condition, token=token) # API専用をコメントアウト
    scraped_candidates = hybrid_ebay_search(keyword, market_id, condition=condition)
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

    # judge_similarity 用にフォーマット調整（_cand_idx を付与して商品単位集約を有効化）
    for i, c in enumerate(scraped_candidates):
        c["img_urls"] = [c["img_url"]]
        c["_cand_idx"] = i

    # 1.5 タイトルフィルター（画像判定前）
    if model_number:
        import re as _re
        VARIANT_PATTERN = _re.compile(
            r'\b(G\d+|Mark\s*[IVX]+|MK\s*[23456]|MK\s*[IVX]+|Ver\.?\s*\d+|第\d+世代|Gen\.?\s*\d+)\b',
            _re.IGNORECASE
        )
        # ハイフン・スペース両方に対応するため、正規化してからトークン分割
        _model_norm = model_number.lower().replace("-", " ")
        model_tokens = [t for t in _model_norm.split() if len(t) >= 2]
        allowed_variants = set(v.lower() for v in VARIANT_PATTERN.findall(model_number))

        clean, variant_ng = [], []
        for c in scraped_candidates:
            title_raw = c.get("title", "").lower()
            # ハイフンをスペースに変換した版と、ハイフンを除去した版の両方で照合
            title_spaced = title_raw.replace("-", " ")
            title_nohyphen = title_raw.replace("-", "")
            # モデルトークンもハイフン除去版を用意
            model_tokens_nohyphen = [t.replace("-", "") for t in model_tokens]
            # いずれかの形式で全トークンが含まれていればOK
            match_spaced = all(t in title_spaced for t in model_tokens)
            match_nohyphen = all(t in title_nohyphen for t in model_tokens_nohyphen)
            if not (match_spaced or match_nohyphen):
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

    # 1.8 ターゲット自身を特別枠として抽出（画像判定・LLM検証をスキップ）
    self_candidate = None
    if exclude_id:
        remaining = []
        for c in scraped_candidates:
            if str(c.get("itemId", "")) == str(exclude_id):
                self_candidate = c
                print(f"    [*] ターゲット自身(ID:{exclude_id})を特別枠として抽出しました。")
            else:
                remaining.append(c)
        scraped_candidates = remaining

    # キャッシュ済み商品と未処理商品を分離
    cached_candidates = []
    uncached_candidates = []
    for c in scraped_candidates:
        iid = str(c.get("itemId", ""))
        if iid in _item_judge_cache:
            cached = _item_judge_cache[iid]
            c["score"] = cached["score"]
            c["best_img_url"] = cached["best_img_url"]
            cached_candidates.append(c)
        else:
            uncached_candidates.append(c)

    cached_count = len(cached_candidates)
    if cached_count:
        print(f"    [*] {market_id}: {cached_count} 件はキャッシュから再利用（詳細画像取得・DINOv2スキップ）。")

    # 未処理分のみ詳細画像取得・DINOv2判定を実行
    if uncached_candidates:
        browser = get_browser_page()
        if browser:
            print(f"    [*] {market_id}: 詳細ページから複数画像を取得中（{len(uncached_candidates)}件）...")
            for c in uncached_candidates:
                item_id = c.get("itemId", "")
                if not item_id:
                    continue
                try:
                    specs = scrape_ebay_item_specs(item_id, browser)
                    detail_imgs = [u for u in specs.get("img_urls", []) if u]
                    if detail_imgs:
                        c["img_urls"] = detail_imgs
                        c["best_img_url"] = detail_imgs[0]
                except Exception as e:
                    print(f"    [!] 詳細画像取得失敗 ({item_id}): {e}")

        print(f"    [*] {market_id}: {len(uncached_candidates)} 件の候補を画像判定中...")

        server_payload = []
        for idx, cand in enumerate(uncached_candidates):
            target_urls = cand.get("img_urls", [])
            if not target_urls and cand.get("img_url"):
                target_urls = [cand["img_url"]]
            for img_url_target in target_urls:
                server_payload.append({
                    "img_url": img_url_target,
                    "page_url": cand.get("item_url"),
                    "_cand_idx": idx
                })

        judged_list, thresholds = _judge_similarity_safe(ref_img, server_payload, base_thresholds)
    else:
        judged_list, thresholds = ([], {})

    # スコアを商品ごとに集約（未処理分のみ・複数画像のうち最高スコアを採用）
    LLM_SCORE_THRESHOLD = 60.0
    top_matches = []

    # 未処理分: DINOv2結果から集約してキャッシュ保存
    for idx, cand in enumerate(uncached_candidates):
        cand_scores = [float(item.get("score", 0)) for item in judged_list if item.get("_cand_idx") == idx]
        best_score = max(cand_scores) if cand_scores else 0
        iid = str(cand.get("itemId", ""))

        if best_score > 0:
            cand["score"] = best_score
            best_item_ji = next(item for item in judged_list if item.get("_cand_idx") == idx and float(item.get("score", 0)) == best_score)
            cand["best_img_url"] = best_item_ji.get("img_url")
            # キャッシュ保存
            _item_judge_cache[iid] = {"score": best_score, "best_img_url": cand["best_img_url"]}
            
            if best_score < LLM_SCORE_THRESHOLD:
                print(f"    [SCORE SKIP] DINOスコア不足({best_score:.1f} < {LLM_SCORE_THRESHOLD}): {cand.get('title','')[:40]}")
                continue
            top_matches.append(cand)

    # キャッシュ済み分: スコア足切りのみ適用して追加
    for cand in cached_candidates:
        best_score = cand.get("score", 0)
        if best_score < LLM_SCORE_THRESHOLD:
            print(f"    [SCORE SKIP(cache)] DINOスコア不足({best_score:.1f} < {LLM_SCORE_THRESHOLD}): {cand.get('title','')[:40]}")
            continue
        top_matches.append(cand)
    
    if not top_matches:
        if self_candidate is None:
            print(f"    [-] 画像判定で合格した候補がありませんでした。")
            return []
        else:
            top_matches = []
    
    # 適合率順にソート
    top_matches.sort(key=lambda x: x.get("score", 0), reverse=True)
    
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

    # ===== 特別枠（ターゲット自身）の配送チェック =====
    if self_candidate is not None:
        self_id = self_candidate.get("itemId", "")
        formatted_self_id = f"v1|{self_id}|0" if "|" not in self_id else self_id
        self_details = get_item_details(token, formatted_self_id, market_id)
        if self_details:
            self_shipping = get_valid_shipping_cost(self_details)
            if self_shipping is None:
                print(f"    [SELF SKIP] ターゲット自身(ID:{self_id}) - 指定国({market_id})への配送不可のため特別枠を除外。")
                self_candidate = None
            else:
                ship_val, ship_currency = self_shipping
                price_val = float(self_details.get("price", {}).get("value", 0))
                price_currency = self_details.get("price", {}).get("currency", "USD")
                total_usd = price_val + ship_val
                print(f"    [SELF OK] ターゲット自身(ID:{self_id}) - 配送可 (本体 {price_val} + 送料 {ship_val} {ship_currency}) -> 合計 ${total_usd:.2f} USD")
                self_candidate["price"] = price_val
                self_candidate["currency"] = price_currency
                self_candidate["shipping"] = ship_val
                self_candidate["ship_currency"] = ship_currency
                self_candidate["total_usd"] = total_usd
                self_candidate["score"] = 100.0
                self_candidate["is_self"] = True
        else:
            print(f"    [SELF SKIP] ターゲット自身(ID:{self_id}) - API詳細取得失敗のため特別枠を除外。")
            self_candidate = None

    # ===== LLM型番一致検証（通常候補のみ・特別枠はスキップ）=====
    if model_number and final_candidates:
        print(f"    [*] {market_id}: LLM型番検証を開始します（型番: {model_number}）...")
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _verify_one(cand):
            iid = str(cand.get("itemId", ""))
            # LLM判定結果もキャッシュ済みなら再利用
            if iid in _item_judge_cache and "llm_match" in _item_judge_cache[iid]:
                cached_match = _item_judge_cache[iid]["llm_match"]
                print(f"    [LLM CACHE] {'✅ 一致' if cached_match else '❌ 不一致'}（キャッシュ再利用）: {cand.get('title','')[:40]}")
                return cand, cached_match, ""
            
            cand_img = (cand.get("best_img_url")
                        or next((u for u in cand.get("img_urls", []) if u), None))
            if not cand_img:
                return cand, True, ""
            is_match, condition_val = verify_model_match(ref_img, cand_img, model_number, condition_text=cand.get("condition", ""), ref_title=ebay_title, cand_title=cand.get("title", ""))
            
            # LLM判定結果をキャッシュ
            if iid in _item_judge_cache:
                _item_judge_cache[iid]["llm_match"] = is_match
            else:
                _item_judge_cache[iid] = {"score": cand.get("score", 0), "best_img_url": cand.get("best_img_url", ""), "llm_match": is_match}
            
            return cand, is_match, condition_val

        verified = []
        with _TPE(max_workers=min(len(final_candidates), 5)) as ex:
            for cand, is_match, _ in ex.map(_verify_one, final_candidates):
                if is_match:
                    verified.append(cand)
                else:
                    print(f"    [LLM REJECT] 型番不一致のため除外: {cand.get('title','')[:50]}")

        final_candidates = verified

    # ===== 特別枠をマージしてTop3を決定 =====
    if self_candidate is not None:
        final_candidates.append(self_candidate)
        print(f"    [SELF ENTRY] ターゲット自身を特別枠としてTop3選考に追加します。")

    final_candidates.sort(key=lambda x: x["total_usd"])
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
                print(f"    - URL:      {hyperlink(res['item_url'])}")
    print("="*70)

if __name__ == "__main__":
    run_test()
