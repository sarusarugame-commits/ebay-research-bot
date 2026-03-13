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
        
        soup = BeautifulSoup(page.html, 'html.parser')
        
        _s_item_count = len(soup.select('.s-item'))
        _li_count = len(soup.select('ul.srp-results li'))
        _viewport_count = len(soup.select('li[data-viewport]'))
        
        # -------------------------------------------------------------------
        # eBay は HTML 構造を頻繁に変更する。最も多くヒットするセレクターを使用する。
        # -------------------------------------------------------------------
        candidates = {
            'li.s-item': soup.select('li.s-item'),
            'ul li[data-viewport]': soup.select('ul.srp-results li[data-viewport]'),
            'li[data-viewport]': soup.select('li[data-viewport]'),
            'ul.srp-results li': soup.select('ul.srp-results li'),
        }
        best_key = max(candidates, key=lambda k: len(candidates[k]))
        item_elements = candidates[best_key]
        print(f"[DEBUG] 使用セレクター: {best_key} ({len(item_elements)}件) / .s-item={_s_item_count}, ul li={_li_count}, viewport={_viewport_count}", flush=True)
        
        items = []
        for elem in item_elements:
            link_tag = elem.select_one('a[href*="/itm/"]')
            if not link_tag:
                continue
            
            url = link_tag.get('href', '')
            item_id_match = re.search(r'/itm/(\d+)', url)
            item_id = item_id_match.group(1) if item_id_match else None
            if not item_id:
                continue

            title = ""
            for sel in ['.s-item__title', 'h3', '.srp-item-title', 'h2']:
                t = elem.select_one(sel)
                if t:
                    title = t.get_text(strip=True)
                    break
            if not title:
                title = link_tag.get('aria-label') or link_tag.get_text(strip=True) or "タイトル不明"
            
            if "Shop on eBay" in title:
                continue

            price_tag = elem.select_one('.s-item__price, [itemprop="price"]')
            price = price_tag.get_text(strip=True) if price_tag else "N/A"

            img_tag = elem.select_one('img')
            image_url = ""
            if img_tag:
                image_url = img_tag.get('data-src') or img_tag.get('src') or ""
            
            items.append({
                'id': item_id,
                'title': title,
                'price': price,
                'url': url,
                'image_url': image_url,
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
