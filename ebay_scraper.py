from DrissionPage import ChromiumPage, ChromiumOptions
from bs4 import BeautifulSoup
import time
import re
import os
import random

# ======================================================================
# ⚙️ 動作モード設定（True/False で切り替え）
# ======================================================================
# True  = クライアント仕様: 常に ebay.com(US) を使い、Ship to だけをUS/UKに切り替える（通貨はUSD）
# False = 本来の仕様: USは ebay.com、UKは ebay.co.uk の現地サイトを使い分ける
USE_STRICT_CLIENT_MODE = True
# ======================================================================

def get_browser_page():
    """DrissionPageのインスタンスを生成する (共通設定)"""
    try:
        co = ChromiumOptions()
        # プロファイルパスなどを固定したい場合はここに追加
        # co.set_user_data_path(r'C:\Users\YourUser\AppData\Local\Google\Chrome\User Data\Default')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1280,720')
        # ヘッドレスモードで使用
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
        
        # ログイン勧誘などのポップアップ (閉じるボタンがあればクリック)
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
        # 読み込み待機 & ポップアップ処理
        page.wait.load_start()
        time.sleep(3) # JavaScript描画待ち
        handle_ebay_popups(page)
        
        # スクロールして全商品をロード (一部の画像はスクロールしないと現れない)
        page.scroll.down(2000)
        time.sleep(2)
        
        soup = BeautifulSoup(page.html, 'html.parser')
        
        # 商品リストの抽出 (eBay検索結果の共通構造)
        # s-item クラスが各商品ブロック
        items = []
        for s_item in soup.select('.s-item'):
            # 「Shop on eBay」などのダミー広告枠を除去
            if "Shop on eBay" in s_item.get_text():
                continue
            
            # ID
            title_tag = s_item.select_one('.s-item__title')
            link_tag = s_item.select_one('.s-item__link')
            
            if not title_tag or not link_tag:
                continue
            
            title = title_tag.get_text(strip=True)
            url = link_tag.get('href')
            
            # URLから item ID を抽出
            item_id_match = re.search(r'/itm/(\d+)', url)
            item_id = item_id_match.group(1) if item_id_match else None
            
            if not item_id: continue

            # 価格
            price_tag = s_item.select_one('.s-item__price')
            price = price_tag.get_text(strip=True) if price_tag else "N/A"
            
            # 画像
            img_tag = s_item.select_one('.s-item__image-img img')
            image_url = ""
            if img_tag:
                # 属性 src, data-src, src-set などから最適なものを拾う
                image_url = img_tag.get('data-src') or img_tag.get('src') or ""
            
            items.append({
                'id': item_id,
                'title': title,
                'price': price,
                'url': url,
                'image_url': image_url,
                'timestamp': time.time() # 便宜上の取得時刻
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
        
        # 読み込み待機
        tab.wait.load_start()
        time.sleep(2)
        
        # 商品情報の抽出
        soup = BeautifulSoup(tab.html, 'html.parser')
        
        # タイトルの取得
        title_tag = soup.select_one('.x-item-title__mainTitle')
        title = title_tag.get_text(strip=True) if title_tag else "不明"
        
        # 価格の取得 (USD)
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
        
        # スペック表 (Item Specifics) を解析
        spec_text = ""
        spec_section = soup.select_one('.ux-layout-section--item-specifics, .item-specifics')
        if spec_section:
            spec_text += spec_section.get_text(" ", strip=True)
            
        # 商品説明 (iframe内) を解析
        desc_frame = tab.ele('#desc_ifr', timeout=2)
        if desc_frame:
            try:
                desc_html = desc_frame.inner_html
                desc_soup = BeautifulSoup(desc_html, 'html.parser')
                spec_text += " " + desc_soup.get_text(" ", strip=True)
            except:
                pass
        
        # 重力と重量のキーワードで正規表現抽出
        w_match = re.search(r'(?:Weight|Mass|重量)[:：\s]*([\d\.]+\s*(?:kg|g|lb|oz|キロ|グラム))', spec_text, re.I)
        if w_match: specs["weight"] = w_match.group(1)
        
        # 寸法
        d_match = re.search(r'(?:Dimensions|Size|サイズ|外寸)[:：\s]*([\d\.]+\s*[x*×]\s*[\d\.]+\s*[x*×]\s*[\d\.]+\s*(?:cm|mm|in|センチ|ミリ))', spec_text, re.I)
        if d_match: specs["dimensions"] = d_match.group(1)
        
        # 追加画像URLの抽出
        img_urls = []
        for img in soup.select('.ux-image-filmstrip-carousel img, .picture-panel img'):
            src = img.get('data-src') or img.get('src')
            if src and "s-l" in src:
                high_res = re.sub(r's-l\d+', 's-l500', src)
                if high_res not in img_urls:
                    img_urls.append(high_res)
        
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
        return {"weight": "不明", "dimensions": "不明", "img_urls": []}
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
    # テスト
    page = get_browser_page()
    test_url = "https://www.ebay.com/sch/i.html?_nkw=watch&_sop=10"
    items = scrape_ebay_newest_items(test_url, page)
    if items:
        specs = scrape_ebay_item_specs(items[0]['id'], page)
        print(specs)
    if page: page.quit()
