import requests, base64, json
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET

def diag_search(query):
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    auth_str = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {b64_auth}"}
    payload = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    token = requests.post(url, headers=headers, data=payload).json().get("access_token")

    search_url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS"
    }
    
    # Test 1: Full Query + Condition
    params1 = {"q": query, "limit": 5, "filter": "conditions:{NEW}"}
    r1 = requests.get(search_url, headers=headers, params=params1).json()
    print(f"Test 1 (Full + NEW): {r1.get('total', 0)} results")
    
    # Test 2: Full Query No Filter
    params2 = {"q": query, "limit": 5}
    r2 = requests.get(search_url, headers=headers, params=params2).json()
    print(f"Test 2 (Full No Filter): {r2.get('total', 0)} results")
    
    # Test 3: Short Query No Filter
    short_query = "Oceanus S6000"
    params3 = {"q": short_query, "limit": 5}
    r3 = requests.get(search_url, headers=headers, params=params3).json()
    print(f"Test 3 (Short Query): {r3.get('total', 0)} results")
    
    if "itemSummaries" in r2:
        print("\nTitles from Test 2:")
        for it in r2["itemSummaries"]:
            print(f"- {it.get('title')[:50]} | Condition: {it.get('condition')}")

diag_search("CASIO Oceanus OCW-S6000")
