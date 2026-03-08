import requests
import base64
import json
import time
import os
import sys

# パス追加 (clip_judge を読み込むため)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import EBAY_APP_ID, EBAY_CLIENT_SECRET
from clip_judge import judge_similarity

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

def search_ebay_market(token, keywords, marketplace_id, condition_str="NEW"):
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=US,zip=10001" if marketplace_id=="EBAY_US" else "contextualLocation=country=GB,zip=E1 6AN"
    }
    params = {
        "q": keywords,
        "limit": 60,
        "filter": f"conditions:{{{condition_str.upper()}}}"
    }
    print(f"[*] eBay {marketplace_id} 検索中... ({condition_str})")
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
        print(f"    [DEBUG] URL: {response.url}")
        print(f"    [DEBUG] Response Code: {response.status_code}")
        if response.status_code == 200:
            return response.json().get("itemSummaries", [])
        else:
            print(f"    [!] eBay API エラー ({response.status_code}): {response.text}")
            return []
    except Exception as e:
        print(f"    [!] eBay 検索中に例外発生: {e}")
        return []

def convert_to_usd(value, currency):
    val = float(value)
    if currency == "USD": return val
    if currency == "GBP": return val * GBP_TO_USD
    return val

def get_item_details(token, item_id, marketplace_id):
    # Browse API: Get Item
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
    except:
        pass
    return None

def process_market(token, market_id, query, ref_img, condition):
    items = search_ebay_market(token, query, market_id, condition)
    if not items: return []
    
    print(f"    [*] {market_id}: {len(items)} 件の商品を検索一覧から取得。")
    
    initial_candidates = []
    for itm in items:
        p_data = itm.get("price", {})
        price_val = float(p_data.get("value", 0))
        price_currency = p_data.get("currency", "USD")
        
        main_img = itm.get("image", {}).get("imageUrl")
        add_imgs = itm.get("additionalImageUrls", [])
        img_urls = [main_img] if main_img else []
        img_urls.extend(add_imgs[:2])
        
        initial_candidates.append({
            "itemId": itm.get("itemId"),
            "title": itm.get("title"),
            "price": price_val,
            "currency": price_currency,
            "img_urls": img_urls,
            "item_url": itm.get("itemWebUrl"),
            "raw_itm": itm
        })

    if not initial_candidates: return []
    
    print(f"    [*] {market_id}: {len(initial_candidates)} 件の候補を画像判定中...")
    judged = judge_similarity(ref_img, initial_candidates)
    
    top_matches = [m for m in judged if m.get("score", 0) >= 70]
    if not top_matches: return []
    
    top_matches.sort(key=lambda x: x["score"], reverse=True)
    
    final_candidates = []
    print(f"    [*] {market_id}: {len(top_matches)} 件の候補の詳細情報を取得して送料を確定中...")
    
    for m in top_matches:
        item_id = m.get("itemId")
        print(f"      [DEBUG] Detail Fetching for {item_id}...")
        details = get_item_details(token, item_id, market_id)
        
        ship_val = 0.0
        ship_currency = "USD"
        found_ship = False
        
        if details:
            ship_options = details.get("shippingOptions", [])
            if not ship_options:
                print(f"        [-] No shipping options (Empty []), skipping item.")
                found_ship = False
            else:
                valid_opts = [o for o in ship_options if o.get("shippingCostType") in ["FIXED", "CALCULATED"] and "shippingCost" in o]
                
                auth_opts = []
                for o in ship_options:
                    svc_code = (o.get("shippingServiceCode") or "").lower()
                    svc_name = (o.get("localizedShippingServiceName") or o.get("shippingServiceName") or "").lower()
                    if "authenticator" in svc_code or "authenticator" in svc_name:
                        auth_opts.append(o)
                
                if auth_opts:
                    opt = auth_opts[0]
                    sc = opt.get("shippingCost", {})
                    ship_val = float(sc.get("value", 0))
                    ship_currency = sc.get("currency", "USD")
                    print(f"        [+] Authenticity Guarantee Fee found: {ship_val} {ship_currency}")
                    found_ship = True
                elif valid_opts:
                    valid_opts.sort(key=lambda o: float(o.get("shippingCost", {}).get("value", 0)))
                    opt = valid_opts[0]
                    sc = opt.get("shippingCost", {})
                    ship_val = float(sc.get("value", 0))
                    ship_currency = sc.get("currency", "USD")
                    print(f"        [+] Selected: {ship_val} {ship_currency} ({opt.get('shippingServiceCode', 'Unknown')})")
                    found_ship = True
        
        if not found_ship:
            itm = m.get("raw_itm", {})
            ship_options = itm.get("shippingOptions", [])
            valid_opts = [o for o in ship_options if o.get("shippingCostType") in ["FIXED", "CALCULATED"] and "shippingCost" in o]
            if valid_opts:
                opt = valid_opts[0]
                sc = opt.get("shippingCost", {})
                ship_val = float(sc.get("value", 0))
                ship_currency = sc.get("currency", "USD")
                print(f"        [+] Summary Fallback: {ship_val} {ship_currency}")
                found_ship = True
        
        if not found_ship:
            print(f"        [-] No valid shipping found for {item_id}, skipping.")
            continue

        total_usd = convert_to_usd(m["price"], m["currency"]) + convert_to_usd(ship_val, ship_currency)
        
        m["shipping"] = ship_val
        m["ship_currency"] = ship_currency
        m["total_usd"] = total_usd
        final_candidates.append(m)

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

    if us_top3 and not uk_top3:
        print("[*] UK の結果が空のため、US の結果を UK にミラーリングします。")
        uk_top3 = us_top3.copy()
    elif uk_top3 and not us_top3:
        print("[*] US の結果が空のため、UK の結果を US にミラーリングします。")
        us_top3 = uk_top3.copy()

    print("\n" + "="*70)
    print("   eBay Global Highest & Best Results (Top 3 Each)")
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
