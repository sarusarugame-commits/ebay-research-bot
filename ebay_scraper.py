from DrissionPage import ChromiumPage, ChromiumOptions
from bs4 import BeautifulSoup
import time
import re
import json
import os
import random

USE_STRICT_CLIENT_MODE = True

def get_browser_page():
    """DrissionPageのインスタンスを生成する"""
    try:
        co = ChromiumOptions()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1280,720')
        co.headless(True)
        page = ChromiumPage(co)
        return page
    except Exception as e:
        print(f"[!] ブラウザ起動失敗: {e}")
        return None

def handle_ebay_popups(tab):
    """eBay特有のポップアップやGDPR通知を閉じる"""
    try:
        btn_gdpr = tab.ele('#gdpr-banner-accept', timeout=2)
        if btn_gdpr: btn_gdpr.click()
        btn_close = tab.ele('xpath://button[@aria-label="Close"]', timeout=2)
        if btn_close: btn_close.click()
    except:
        pass

def scrape_ebay_newest_items(search_url, page):
    """
    指定されたURLから eBay の新着商品をスクレイピングする
    """
    print(f"[*] eBayアクセス開始: {search_url}", flush=True)
    try:
        page.get(search_url)
        page.wait.load_start()
        time.sleep(3)
        handle_ebay_popups(page)
        page.scroll.down(2000)
        time.sleep(2)
        
        html = page.html
        
        # -------------------------------------------------------------------
        # eBayの最新仕様: HTMLタグ（DOM）の中に直接商品が描画されず、
        # <a href="/itm/..."> などの実体がダミー（/itm/123456...など）になるケースが頻発する。
        # 解決策: HTMLソース全体から「/itm/12ケタ以上の数字」のURLを根こそぎ正規表現で抜き出し、
        # 重複やダミーを排除してアイテムリストを構築する。
        # -------------------------------------------------------------------
        
        url_pattern = r'(https://www\.ebay\.com/itm/\d{12,}[^\s\"\'\\]*)'
        raw_urls = re.findall(url_pattern, html)
        
        unique_ids = {}
        for u in raw_urls:
            m = re.search(r'/itm/(\d{12,})', u)
            if m:
                item_id = m.group(1)
                # ダミーIDは除外
                if item_id in ['123456789012', '1234567890123'] or item_id.startswith('123456'):
                    continue
                if item_id not in unique_ids:
                    # クエリパラメータを取り除いてユニークなURLにする
                    clean_url = u.split('?')[0] if '?' in u else u
                    unique_ids[item_id] = clean_url
        
        print(f"[DEBUG] HTML内から固有の商品IDを {len(unique_ids)} 件抽出しました。", flush=True)

        items = []
        for item_id, url in unique_ids.items():
            items.append({
                'id': item_id,
                'title': f"eBay Item {item_id}", # プレースホルダー（後で詳細ページで取得）
                'price': "N/A",                  # プレースホルダー
                'url': url,
                'image_url': "",
                'timestamp': time.time()
            })
            
        print(f" -> {len(items)} 件の商品を抽出しました。", flush=True)
        return items
        
    except Exception as e:
        print(f"[!] スクレイピング失敗: {e}", flush=True)
        return []

def scrape_ebay_item_specs(item_id, browser):
    """eBay詳細からスペック抽出"""
    url = f"https://www.ebay.com/itm/{item_id}"
    print(f"[*] eBay詳細読込中: {item_id}...", flush=True)
    tab = None
    try:
        tab = browser.new_tab(url)
        tab.get(url, timeout=15)
        handle_ebay_popups(tab)
        tab.wait.load_start()
        time.sleep(2)
        
        soup = BeautifulSoup(tab.html, 'html.parser')
        
        title_tag = soup.select_one('.x-item-title__mainTitle')
        title = title_tag.get_text(strip=True) if title_tag else "不明"
        
        price_tag = soup.select_one('.x-price-primary span, #prclbl')
        price_str = price_tag.get_text(strip=True) if price_tag else "0"
        price_val = re.sub(r'[^\d\.]', '', price_str)
        price_usd = float(price_val) if price_val else 0.0

        specs = {
            "title": title,
            "price_usd": price_usd,
            "weight": "不明",
            "dimensions": "不明",
            "img_urls": []
        }
        
        spec_text = ""
        spec_section = soup.select_one('.ux-layout-section--item-specifics, .item-specifics')
        if spec_section:
            spec_text += spec_section.get_text(" ", strip=True)
            
        desc_frame = tab.ele('#desc_ifr', timeout=2)
        if desc_frame:
            try:
                desc_html = desc_frame.inner_html
                desc_soup = BeautifulSoup(desc_html, 'html.parser')
                spec_text += " " + desc_soup.get_text(" ", strip=True)
            except:
                pass
        
        w_match = re.search(r'(?:Weight|Mass|重量)[:：\s]*([\d\.]+\s*(?:kg|g|lb|oz|キロ|グラム))', spec_text, re.I)
        if w_match: specs["weight"] = w_match.group(1)
        
        d_match = re.search(r'(?:Dimensions|Size|サイズ|外寸)[:：\s]*([\d\.]+\s*[x*×]\s*[\d\.]+\s*[x*×]\s*[\d\.]+\s*(?:cm|mm|in|センチ|ミリ))', spec_text, re.I)
        if d_match: specs["dimensions"] = d_match.group(1)
        
        # 画像の取得 (複数セレクターでフォールバック)
        img_urls = []
        
        # 1. フィルムストリップ / ピクチャーパネル / カルーセル
        for img in soup.select('.ux-image-filmstrip-carousel img, .picture-panel img, .ux-image-carousel-item img'):
            src = img.get('data-src') or img.get('data-zoom-src') or img.get('src') or ''
            if src and src.startswith('http') and 's-l' in src:
                high_res = re.sub(r's-l\d+', 's-l500', src)
                if high_res not in img_urls:
                    img_urls.append(high_res)
        
        # 2. JSON-LD の image フィールド
        if not img_urls:
            for script_tag in soup.select('script[type="application/ld+json"]'):
                try:
                    data = json.loads(script_tag.string or '')
                    if isinstance(data, dict):
                        imgs = data.get('image', [])
                        if isinstance(imgs, str): imgs = [imgs]
                        img_urls.extend([u for u in imgs if u.startswith('http')])
                except:
                    pass
        
        # 3. og:image (Open Graph)
        if not img_urls:
            og_img = soup.select_one('meta[property="og:image"]')
            if og_img and og_img.get('content'):
                img_urls.append(og_img['content'])
        
        # 4. 一般的なメイン画像セレクター
        if not img_urls:
            main_img = soup.select_one('.ux-image-magnifier-view img, #mainImgH0')
            if main_img:
                src = main_img.get('src')
                if src: img_urls.append(re.sub(r's-l\d+', 's-l500', src))

        img_urls = [u for u in img_urls if u and u.startswith('http')]
        specs["img_urls"] = img_urls
        
        print(f" -> スペック取得完了（画像{len(img_urls)}枚）。", flush=True)
        return specs
    except Exception as e:
        print(f"[!] 詳細パース失敗: {e}", flush=True)
        return {"title": "不明", "price_usd": 0.0, "weight": "不明", "dimensions": "不明", "img_urls": []}
    finally:
        if tab:
            try: tab.close()
            except: pass

def scrape_ebay_seller_items(seller_id, browser):
    """
    特定セラーの出品一覧を取得する
    """
    url = f"https://www.ebay.com/sch/i.html?_ssn={seller_id}&_sop=10"
    return scrape_ebay_newest_items(url, browser)

if __name__ == "__main__":
    page = get_browser_page()
    test_url = "https://www.ebay.com/sch/i.html?_nkw=watch&_sop=10"
    items = scrape_ebay_newest_items(test_url, page)
    if items:
        specs = scrape_ebay_item_specs(items[0]['id'], page)
        print(specs)
    if page: page.quit()
