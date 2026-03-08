import requests
import base64
import json
import os
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET

def get_token():
    auth_str = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {b64_auth}"}
    payload = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    return requests.post(url, headers=headers, data=payload).json().get("access_token")

def analyze_market(market_id, country_code):
    token = get_token()
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": market_id,
        "X-EBAY-C-ENDUSERCTX": f"contextualLocation=country%3D{country_code}"
    }
    # オフィシャルな商品を狙って検索
    params = {"q": "CASIO Oceanus OCW-S6000", "limit": 20}
    
    print(f"\n--- Analyzing {market_id} for {country_code} ---")
    resp = requests.get(url, headers=headers, params=params)
    items = resp.json().get("itemSummaries", [])
    
    for itm in items:
        title = itm.get("title")[:50]
        price = itm.get("price", {})
        ship_opts = itm.get("shippingOptions", [])
        
        print(f"Title: {title}")
        print(f"  Price: {price.get('value')} {price.get('currency')}")
        
        if not ship_opts:
            print("  [!] EMPTY shippingOptions (Does not ship?)")
        else:
            for i, opt in enumerate(ship_opts):
                cost = opt.get("shippingCost", {})
                cost_val = cost.get("value", "MISSING")
                cost_curr = cost.get("currency", "MISSING")
                ship_type = opt.get("shippingCostType", "UNKNOWN")
                print(f"  Opt {i}: {ship_type} | Cost: {cost_val} {cost_curr}")
                # Some items might have 'CALCULATED' but no direct cost value shown in summary?

if __name__ == "__main__":
    analyze_market("EBAY_US", "US")
    analyze_market("EBAY_GB", "GB")
