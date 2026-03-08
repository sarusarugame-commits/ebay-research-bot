import requests
import base64
import json
import time
import os
import sys
import urllib.parse
import re

# パス追加 (clip_judge を読み込むため)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import EBAY_APP_ID, EBAY_CLIENT_SECRET
from clip_judge import judge_similarity
from llm_vision_judge import verify_model_match
from ebay_scraper import get_browser_page

# 為替レート (簡易的な固定値)
GBP_TO_USD = 1.25

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

def convert_to_usd(value, currency):
    val = float(value)
    if currency == "USD": return val
    if currency == "GBP": return val * GBP_TO_USD
    return val

def extract_item_id(url):
    """URLからeBayのItem IDを抽出する"""
    if not url: return None
    match = re.search(r'/itm/(\d+)', url)
    return match.group(1) if match else None

def get_valid_shipping_cost(details):
    """APIの詳細データから、有効な送料を抽出する"""
    ship_options = details.get("shippingOptions", [])
    if not ship_options:
        return None  # 配送オプションなし（配送不可）

    # 固定費または計算済みの送料を探す
    valid_opts = [o for o in ship_options if o.get("shippingCostType") in ["FIXED", "CALCULATED"] and "shippingCost" in o]
    
    # 鑑定サービス(Authenticity Guarantee)がある場合は優先
    for o in ship_options:
        svc_code = (o.get("shippingServiceCode") or "").lower()
        svc_name = (o.get("localizedShippingServiceName") or o.get("shippingServiceName") or "").lower()
        if "authenticator" in svc_code or "authenticator" in svc_name:
            sc = o.get("shippingCost", {})
            return float(sc.get("value", 0)), sc.get("currency", "USD")

    if valid_opts:
        # 最も安い配送方法を選ぶ
        valid_opts.sort(key=lambda o: float(o.get("shippingCost", {}).get("value", 0)))
        opt = valid_opts[0]
        sc = opt.get("shippingCost", {})
        return float(sc.get("value", 0)), sc.get("currency", "USD")
    
    return None # 配送不可

def hybrid_ebay_search(keyword, market_id="EBAY_US"):
    """
    1. スクレイピングで正しく並んだ候補を取得
    2. APIでUS/UKへの配送可否と正確な送料を検証して返す
    """
    is_us = (market_id == "EBAY_US")
    domain = "ebay.com" if is_us else "ebay.co.uk"
    safe_keyword = urllib.parse.quote_plus(keyword)
    
    # _sop=15: Price + Shipping: lowest first, LH_ItemCondition=1000: New
    url = f"https://www.{domain}/sch/i.html?_nkw={safe_keyword}&_sop=15&LH_ItemCondition=1000"
    
    browser = get_browser_page()
    if not browser:
        print(f"    [!] ハイブリッド検索: ブラウザが取得できません。")
        return []

    scraped_items = []
    print(f"[*] eBay {market_id} スクレイピング開始 (URL: {url})...")
    try:
        tab = browser.new_tab(url)
        tab.get(url, timeout=20)
        tab.wait(3) # レンダリング待ち
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(tab.html, 'html.parser')
        tab.close()
        
        # --- ebay_scraper.py と同じ「確実な」抽出ロジック ---
        itm_links = soup.find_all('a', href=re.compile(r'/itm/'))
        unique_ids = set()
        
        for link_tag in itm_links:
            if len(scraped_items) >= 60: break # 1ページ分（約60件）をすべて取得
            
            link = link_tag.get('href')
            item_id = extract_item_id(link)
            if not item_id or item_id in unique_ids: continue
            
            # 親要素を遡って商品コンテナを特定
            container = None
            p = link_tag
            for _ in range(10):
                p = p.parent
                if not p: break
                if 's-item' in p.get('class', []) or 's-card' in p.get('class', []):
                    container = p
                    break
            
            if not container: continue
            
            # タイトルの取得
            title_tag = container.find(['h3', 'div', 'span'], class_=re.compile(r'title', re.I))
            title = title_tag.get_text(strip=True) if title_tag else ""
            if not title or "Shop on eBay" in title: continue
            
            # 画像の取得（Lazy Load対応）
            img_tag = container.find('img', class_=re.compile(r'image', re.I)) or container.find('img')
            img_url = ""
            if img_tag:
                img_url = img_tag.get('data-src') or img_tag.get('src') or ""
            
            unique_ids.add(item_id)
            scraped_items.append({
                "itemId": item_id,
                "title": title,
                "item_url": link,
                "img_url": img_url
            })
            
    except Exception as e:
        print(f"    [!] スクレイピング中にエラー: {e}")

    return scraped_items

def get_item_details(token, item_id, marketplace_id):
    url = f"https://api.ebay.com/buy/browse/v1/item/{item_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=US,zip=10001" if marketplace_id=="EBAY_US" else "contextualLocation=country=GB,zip=E1 6AN"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"      [!] get_item_details API Error: {e}")
    return None

def process_market(token, market_id, query, ref_img, condition, model_number=""):
    print(f"\n[*] {market_id} ハイブリッド相場調査を開始します...")
    
    # 1. ハイブリッド検索 (スクレイピングで候補取得)
    scraped_candidates = hybrid_ebay_search(query, market_id)
    if not scraped_candidates:
        print(f"    [-] {market_id}: 有効な候補が見つかりませんでした。")
        return []
    
    # judge_similarity 用にフォーマット調整
    for c in scraped_candidates:
        c["img_urls"] = [c["img_url"]] if c.get("img_url") else []

    # 2. 画像判定 (DINOv2)
    print(f"    [*] {market_id}: {len(scraped_candidates)} 件の候補を画像判定中...")
    judged = judge_similarity(ref_img, scraped_candidates)
    
    top_matches = [m for m in judged if m.get("score", 0) >= 70]
    if not top_matches: 
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
        
        total_usd = convert_to_usd(price_val, price_currency) + convert_to_usd(ship_val, ship_currency)
        
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
        verified = []
        for cand in final_candidates:
            cand_img = cand.get("img_urls", [None])[0]
            if not cand_img:
                verified.append(cand)
                continue

            if verify_model_match(ref_img, cand_img, model_number):
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
