from DrissionPage import ChromiumPage, ChromiumOptions
from selectolax.parser import HTMLParser
from scrapling import Fetcher
from bs4 import BeautifulSoup
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
        # DrissionPage によるページ取得
        page.get(search_url)
        page.wait.load_start()
        time.sleep(3)
        handle_ebay_popups(page)
        
        # --- 重要: JS Hydration（仮想スクロール）によるDOM要素の消失を防ぐ ---
        # eBayはブラウザでJSが実行されると、画面外の商品をDOMから削除してしまいます（数件だけになる原因）。
        # これを回避するため、Bot検知をクリアしたブラウザのCookieを使って、JS実行前の生HTMLを取得します。
        import requests
        try:
            # DrissionPageのバージョン互換性を保つため、as_dictを使わずに手動で辞書型へ変換
            raw_cookies = page.cookies()
            if isinstance(raw_cookies, list):
                cookies = {c['name']: c['value'] for c in raw_cookies if 'name' in c and 'value' in c}
            elif isinstance(raw_cookies, dict):
                cookies = raw_cookies
            else:
                cookies = {}
                
            headers = {
                "User-Agent": page.user_agent,
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
            }
            resp = requests.get(search_url, headers=headers, cookies=cookies, timeout=15)
            raw_html = resp.text
            
            # もし requests が弾かれた場合はブラウザのHTMLにフォールバック
            if "Pardon our interruption" in raw_html or "captcha" in raw_html.lower():
                print("[DEBUG] requestsによる取得がブロックされました。ブラウザのDOMにフォールバックします。")
                page.scroll.down(5000)
                time.sleep(2)
                raw_html = page.html
            else:
                print("[DEBUG] JS実行前の生HTMLを正常に取得しました（仮想スクロール回避）。")
        except Exception as e:
            print(f"[DEBUG] 生HTML取得エラー ({e})。ブラウザのDOMにフォールバックします。")
            page.scroll.down(5000)
            time.sleep(2)
            raw_html = page.html
            
        # eBayはレスポンスHTML内の大部分（商品リスト等）を <!-- --> でコメントアウトして隠蔽するため、
        # パース前に正規表現でコメントタグを削除して要素を活性化させます。
        raw_html = re.sub(r'<!--|-->', '', raw_html)
            
        # 高速かつ堅牢な Selectolax を使用してパース
        tree = HTMLParser(raw_html)
        
        # セレクタの候補をすべて取得して統合する
        item_selectors = [
            '.srp-results li.s-item',
            'li.s-item',
            '.s-card-container',
            '.s-card',
            'li[data-viewport]',
            '.srp-list li'
        ]
        
        all_elements = []
        for selector in item_selectors:
            elements = tree.css(selector)
            if elements:
                all_elements.extend(elements)
                print(f"[DEBUG] '{selector}' セレクタで {len(elements)} 件の候補を検知しました。")
        
        if not all_elements:
            # 最終手段として全ての li 要素を走査
            all_elements = tree.css('li')
            if len(all_elements) > 0:
                print(f"[DEBUG] フォールバックとして全ての 'li' ({len(all_elements)} 件) を調査します。")

        extracted_items = []
        seen_ids = set()

        for elem in all_elements:
            item_id = "N/A"
            try:
                # --- リンクとIDの抽出 ---
                item_url = ""
                
                link_targets = ['a.s-item__link', 'a.s-card__link', 'a[href*="/itm/"]', 'a']
                for l_sel in link_targets:
                    links = elem.css(l_sel)
                    for l in links:
                        # Selectolaxでは辞書型で属性が取得できる
                        href = l.attributes.get('href', '')
                        if href and '/itm/' in href:
                            m = re.search(r'/itm/(\d{12,})', href)
                            if m:
                                item_id = m.group(1)
                                item_url = f"https://www.ebay.com/itm/{item_id}"
                                break
                    if item_id != "N/A": break
                
                if item_id == "N/A" or item_id in seen_ids:
                    continue
                
                # ダミーID除外
                if item_id.startswith('123456'):
                    continue

                # --- タイトルの抽出 ---
                title = ""
                title_targets = [
                    '.s-item__title', '.s-card__title', 
                    '[role="heading"]', 'h3', 'h2', 'h1', 
                    '.s-item__link', 'a'
                ]
                for t_sel in title_targets:
                    # css_firstで安全に最初の要素を取得
                    t_el = elem.css_first(t_sel)
                    if t_el and t_el.text(strip=True):
                        text = t_el.text(strip=True)
                        # "Shop on eBay" や空文字、短すぎるタイトルを除外
                        if text and "Shop on eBay" not in text and len(text) > 10:
                            # "新規出品" などのプレフィックスを除去
                            title = re.sub(r'^(?:新規出品|New Listing)\s*', '', text)
                            break
                
                if not title:
                    continue

                # --- 価格の抽出 ---
                price = "N/A"
                price_targets = [
                    '.s-item__price', '.s-card__price', 
                    '.su-price', '.s-item__primary-price',
                    '.s-card__primary-price', 'span[class*="price"]'
                ]
                for p_sel in price_targets:
                    p_el = elem.css_first(p_sel)
                    if p_el and p_el.text(strip=True):
                        price_text = p_el.text(strip=True)
                        if price_text:
                            price = price_text
                            break

                # --- 画像の抽出 ---
                image_url = ""
                img_el = elem.css_first('img')
                if img_el:
                    image_url = (img_el.attributes.get('data-src') or 
                                 img_el.attributes.get('src') or 
                                 img_el.attributes.get('data-original-src') or "")

                extracted_items.append({
                    'id': item_id,
                    'title': title,
                    'price': price,
                    'url': item_url,
                    'image_url': image_url,
                    'timestamp': time.time()
                })
                seen_ids.add(item_id)
                
            except Exception as e:
                # エラー原因を特定しやすくするためログを出力
                print(f"[DEBUG] パースエラー (ID: {item_id}): {type(e).__name__}: {e}")
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

if __name__ == "__main__":
    page = get_browser_page()
    test_url = "https://www.ebay.com/sch/i.html?_ssn=greenepron&_sop=10"
    items = scrape_ebay_newest_items(test_url, page)
    if items:
        specs = scrape_ebay_item_specs(items[0]['id'], page)
        print(specs)
    if page: page.quit()
