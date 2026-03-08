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

def inspect_types(market_id, country_code):
    token = get_token()
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": market_id,
        "X-EBAY-C-ENDUSERCTX": f"contextualLocation=country%3D{country_code}"
    }
    params = {"q": "watch", "limit": 40}
    
    print(f"\n=== Inspecting {market_id} for {country_code} ===")
    resp = requests.get(url, headers=headers, params=params)
    items = resp.json().get("itemSummaries", [])
    
    for itm in items:
        title = itm.get("title")[:50]
        ship_opts = itm.get("shippingOptions", [])
        if ship_opts:
            print(f"Title: {title}")
            for opt in ship_opts:
                print(f"  - Type: {opt.get('shippingCostType')} | Cost: {opt.get('shippingCost')} | Service: {opt.get('shippingServiceCode')}")

if __name__ == "__main__":
    inspect_types("EBAY_US", "US")
    inspect_types("EBAY_GB", "GB")
