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

def test_shipping_check(country_code):
    token = get_token()
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    # EndUserCtx で場所を指定
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US" if country_code == "US" else "EBAY_GB",
        "X-EBAY-C-ENDUSERCTX": f"contextualLocation=country%3D{country_code}"
    }
    # あえて幅広く検索
    params = {"q": "watch", "limit": 20}
    
    print(f"\n--- Testing Shipping to {country_code} ---")
    resp = requests.get(url, headers=headers, params=params)
    items = resp.json().get("itemSummaries", [])
    
    for itm in items:
        title = itm.get("title")[:40]
        ship_opts = itm.get("shippingOptions", [])
        if not ship_opts:
            print(f"[EXCLUDED] {title} | shippingOptions is empty (Does not ship to {country_code})")
        else:
            cost = ship_opts[0].get("shippingCost", {}).get("value", "0")
            curr = ship_opts[0].get("shippingCost", {}).get("currency", "")
            print(f"[OK] {title} | Cost: {cost} {curr}")

if __name__ == "__main__":
    test_shipping_check("GB") # UK
    test_shipping_check("US") # US
