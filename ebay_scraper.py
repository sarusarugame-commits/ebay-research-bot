from DrissionPage import ChromiumPage, ChromiumOptions
from scrapling.parser import Adaptor
import time
import re
import os
import random
import json

# ======================================================================
# ⚙️ 動作モード設定（True/False で切り替え）
# ======================================================================
# True  = クライアント仕様: 常に ebay.com(US) を使い, Ship to だけをUS/UKに切り替える（通貨はUSD）
# False = 本来の仕様: USは ebay.com, UKは ebay.co.uk の現地サイトを使い分ける
USE_STRICT_CLIENT_MODE = True
# ======================================================================

def get_browser_page():
    """DrissionPageのインスタンスを生成する (共通設定)"""
    try:
        co = ChromiumOptions()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1280,720')
        # ステルス性能向上のための追加オプション
        co.set_argument('--disable-blink-features=AutomationControlled')
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
        co.headless(True)
        page = ChromiumPage(co)
        return page
    except Exception as e:
        print(f"[!] ブラウザ起動失敗: {e}")
        return None

def handle_ebay_popups(tab):
    """eBay特有のポップアップやGDPR通知を閉じる"""
    try:
        # GDPR / Cookie 同意ボタン
        btn_gdpr = tab.ele('#gdpr-banner-accept', timeout=2)
        if btn_gdpr: btn_gdpr.click()
        
        # ログイン勧誘などのポップアップ
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
        # ページ読み込み完了を待機
        page.wait.load_start()
        time.sleep(3)
        handle_ebay_popups(page)
        
        # 動的読み込みを促すためにスクロール
        page.scroll.down(4000)
        time.sleep(2)
        
        # Scrapling を使用してパース
        adaptor = Adaptor(page.html)
        
        # 複数のコンテナセレクタ（s-item, srp-results内のli, および新しい s-card）に対応
        # セレクタを OR 条件で指定
        item_selectors = [
            '.srp-results li.s-item',
            'li.s-item',
            '.s-card-container',
            '.s-card',
            'li[data-viewport]'
        ]
        
        items_found = []
        for selector in item_selectors:
            elements = adaptor.css(selector)
            if elements:
                items_found = elements
                print(f"[DEBUG] '{selector}' セレクタで {len(elements)} 件の商品を検知しました。")
                break
        
        if not items_found:
            # 最終手段として全ての li 要素を走査（より広い網）
            items_found = adaptor.css('li')
            print(f"[DEBUG] フォールバックとして全ての 'li' ({len(items_found)} 件) を調査します。")

        extracted_items = []
        for elem in items_found:
            try:
                # リンクの抽出（URLから商品IDを特定）
                link_el = elem.css('a[href*="/itm/"]').first()
                if not link_el:
                    continue
                
                url = link_el.attributes.get('href', '')
                if not url:
                    continue
                
                # 商品ID (12桁) 抽出
                m = re.search(r'/itm/(\d{12,})', url)
                if not m:
                    continue
                item_id = m.group(1)
                
                # 重複チェック
                if any(x['id'] == item_id for x in extracted_items):
                    continue

                # タイトルの抽出（複数の可能性を試行）
                title = ""
                title_targets = ['.s-item__title', '.s-card__title', 'h3', 'h2', 'span[role="heading"]']
                for t_sel in title_targets:
                    t_el = elem.css(t_sel).first()
                    if t_el and t_el.text:
                        title = t_el.text.strip()
                        break
                
                if not title or "Shop on eBay" in title:
                    continue

                # 価格の抽出
                price = "N/A"
                price_targets = ['.s-item__price', '.s-card__price', '.su-price', '.s-item__primary-price']
                for p_sel in price_targets:
                    p_el = elem.css(p_sel).first()
                    if p_el and p_el.text:
                        price = p_el.text.strip()
                        break

                # 画像の抽出
                image_url = ""
                img_el = elem.css('img').first()
                if img_el:
                    image_url = img_el.attributes.get('data-src') or img_el.attributes.get('src') or ""

                extracted_items.append({
                    'id': item_id,
                    'title': title,
                    'price': price,
                    'url': url,
                    'image_url': image_url,
                    'timestamp': time.time()
                })
            except Exception:
                continue
            
        print(f" -> {len(extracted_items)} 件の本物の商品を抽出しました。", flush=True)
        return extracted_items
        
    except Exception as e:
        print(f"[!] スクレイピング失敗: {e}", flush=True)
        return []

def scrape_ebay_item_specs(item_id, browser):
    """eBay詳細からスペック抽出"""
    from bs4 import BeautifulSoup
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
        
        # 画像取得
        img_urls = []
        for img in soup.select('.ux-image-filmstrip-carousel img, .picture-panel img, .ux-image-carousel-item img'):
            src = img.get('data-src') or img.get('data-zoom-src') or img.get('src') or ''
            if src and src.startswith('http') and 's-l' in src:
                high_res = re.sub(r's-l\d+', 's-l500', src)
                if high_res not in img_urls:
                    img_urls.append(high_res)
        
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
        
        if not img_urls:
            og_img = soup.select_one('meta[property="og:image"]')
            if og_img and og_img.get('content'):
                img_urls.append(og_img['content'])
        
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
    """特定セラーの出品一覧を取得する"""
    url = f"https://www.ebay.com/sch/i.html?_ssn={seller_id}&_sop=10"
    return scrape_ebay_newest_items(url, browser)
