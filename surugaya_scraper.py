import urllib.parse

def search_surugaya(keyword, browser_page, max_results=10):
    """駿河屋検索 (司令官流：DrissionPageネイティブ＆スマート待機版)"""
    encoded_keyword = urllib.parse.quote(keyword)
    url = f"https://www.suruga-ya.jp/search?category=&search_word={encoded_keyword}&is_all=icon_shinchaku&sort=price_asc"
    
    print(f"[*] 駿河屋検索開始: {keyword}")
    results = []
    try:
        browser_page.get(url)
        
        # time.sleep()を廃止して timeout でスマート待機
        items = browser_page.eles('css:.item', timeout=15)
        print(f"[*] {len(items)} 件の商品を駿河屋で読み込みました。")
        
        for item in items:
            if len(results) >= max_results: break
            
            # リンク要素を探す
            a_tag = item.ele('css:a[href*="/product/"]') or item.ele('css:.title a')
            if not a_tag: continue
            
            item_url = a_tag.attr('href')
            if item_url and not item_url.startswith("http"):
                item_url = f"https://www.suruga-ya.jp{item_url}"
            
            title = a_tag.text.strip()
            img_tag = item.ele('tag:img')
            img_url = img_tag.attr('src') if img_tag else ""
            
            price_tag = item.ele('css:.price') or item.ele('css:.text-price') or item.ele('css:.item_price')
            price_text = price_tag.text.strip() if price_tag else "0"
            
            if item_url:
                results.append({
                    "title": title, "page_url": item_url, "img_url": img_url,
                    "price": price_text, "platform": "駿河屋"
                })
        print(f"    [駿河屋] 有効な出品を {len(results)} 件抽出しました。")
    except Exception as e:
        print(f"    [Error] search_surugaya: {e}")
    return results

def scrape_surugaya_item(url, browser_page):
    """駿河屋詳細抽出 (DrissionPageネイティブ版)"""
    print(f"    [SCRAPE_DEBUG] 駿河屋詳細アクセス: {url}")
    try:
        browser_page.get(url)
        
        # h1タグが出るまで賢く待機
        title_tag = browser_page.ele('css:h1#product_name', timeout=10) or browser_page.ele('css:.product_title')
        title = title_tag.text.strip() if title_tag else "不明"
        
        img_urls = []
        # メイン画像エリアから画像を探す
        img_elems = browser_page.eles('css:#view_item_image img') or browser_page.eles('css:.photo_area img') or browser_page.eles('css:.item_main_image img')
        for img in img_elems:
            src = img.attr('src')
            if src:
                if not src.startswith("http"): src = f"https:{src}"
                if src not in img_urls: img_urls.append(src)
            if len(img_urls) >= 5: break
            
        print(f"    [SCRAPE_DEBUG] 駿河屋抽出画像数: {len(img_urls)}")
        
        condition = "中古"
        price_tag = browser_page.ele('css:.text-red') or browser_page.ele('css:.price') or browser_page.ele('css:.item_price')
        price_text = price_tag.text.strip() if price_tag else "0"
        
        return {
            "title": title,
            "img_urls": img_urls,
            "condition": condition,
            "price": price_text
        }
    except Exception as e:
        print(f"    [SCRAPE_DEBUG] 駿河屋エラー: {e}")
        return None
