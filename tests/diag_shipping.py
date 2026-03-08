import requests, base64, json
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET

def diag_shipping(query, market):
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    auth_str = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {b64_auth}"}
    payload = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    token = requests.post(url, headers=headers, data=payload).json().get("access_token")

    search_url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}", 
        "X-EBAY-C-MARKETPLACE-ID": market,
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3D" + ("US" if market=="EBAY_US" else "GB")
    }
    params = {"q": query, "limit": 10, "filter": "conditions:{NEW}"}
    r = requests.get(search_url, headers=headers, params=params).json()
    
    items = r.get("itemSummaries", [])
    print(f"--- Marketplace: {market} ({len(items)} items) ---")
    for it in items:
        title = it.get("title")[:40]
        ship_opts = it.get("shippingOptions", [])
        print(f"Item: {title}")
        if not ship_opts:
            print("  [!] No shippingOptions found")
        for opt in ship_opts:
            cost_type = opt.get("shippingCostType")
            cost_data = opt.get("shippingCost")
            print(f"  - Type: {cost_type} | Cost: {json.dumps(cost_data)}")

diag_shipping("CASIO Oceanus OCW-S6000", "EBAY_US")
print("\n")
diag_shipping("CASIO Oceanus OCW-S6000", "EBAY_GB")
