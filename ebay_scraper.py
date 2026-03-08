import re
import datetime
import sys
import io
import time
from DrissionPage import ChromiumPage, ChromiumOptions
from bs4 import BeautifulSoup

_browser_instance = None

def handle_ebay_popups(tab):
    """eBay特有のポップアップ（配送先確認など）を処理する。"""
    try:
        # 「Where are you shipping to?」 ポップアップ
        # セレクタは画像に基づき、「Confirm」テキストを持つボタンを優先
        confirm_button = tab.ele('text=Confirm', timeout=1.5)
        if confirm_button and confirm_button.is_displayed():
            print("[*] eBay配送先確認ポップアップを検出。'Confirm'をクリックします。", flush=True)
            confirm_button.click()
            tab.wait(1)
        
        # もし「Where are you shipping to?」というテキストがあり、かつ閉じるボタンがあればそれも検討
        # ただしユーザーの指示は「コンファーム押して」なのでConfirmを優先
    except:
        pass

def get_browser_page():
    global _browser_instance
    if _browser_instance:
        try:
            _browser_instance.latest_tab
            return _browser_instance
        except: _browser_instance = None

    co = ChromiumOptions().set_local_port(9222)
    # 全画面起動を抑制し、指定サイズで起動するように設定
    co.set_argument('--window-size=1280,720')
    co.remove_argument('--start-maximized')
    # ポート9222接続時は動作確認しやすくするため headless(False) ですが、全画面は防ぎます
    co.headless(False)

    try:
        print("[*] ブラウザ(9222)に接続中...", flush=True)
        _browser_instance = ChromiumPage(co)
        return _browser_instance
    except Exception as e:
        print(f"[*] ブラウザ(9222)への接続に失敗しました: {type(e).__name__}: {e}", flush=True)
        print("[*] 新規ブラウザを起動中...", flush=True)
        try:
            co_new = ChromiumOptions()
            co_new.headless(True)
            # ブラウザのパスを自動探索させるが、失敗時のログを出す
            _browser_instance = ChromiumPage(co_new)
            return _browser_instance
        except Exception as e2:
            print(f"[!] 新規ブラウザの起動に失敗しました: {e2}", flush=True)
            import traceback
            traceback.print_exc()
            return None

def parse_ebay_date(date_str):
    now = datetime.datetime.now()
    # 記号の正規化
    clean_str = date_str.replace('·', ' ').replace(',', ' ').strip()
    
    # 月の定義 (12ヶ月分)
    month_pattern = r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
    # パターン1: Mar 4 20:37
    m1 = re.search(fr'{month_pattern}\s*[- ]\s*(\d{{1,2}})(\s+(\d{{1,2}}):(\d{{2}}))?', clean_str, re.I)
    # パターン2: 4 Mar 20:37
    m2 = re.search(fr'(\d{{1,2}})\s*[- ]\s*{month_pattern}(\s+(\d{{1,2}}):(\d{{2}}))?', clean_str, re.I)
    
    month_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
    month, day, hour, minute = None, None, 0, 0
    
    if m1:
        month = month_map.get(m1.group(1).lower())
        day = int(m1.group(2))
        if m1.group(4): hour = int(m1.group(4))
        if m1.group(5): minute = int(m1.group(5))
    elif m2:
        month = month_map.get(m2.group(2).lower())
        day = int(m2.group(1))
        if m2.group(4): hour = int(m2.group(4))
        if m2.group(5): minute = int(m2.group(5))
        
    if month and day:
        try:
            dt = datetime.datetime(now.year, month, day, hour, minute)
            # 未来の日付（1日以上先）なら昨年のものと判断
            if dt > now + datetime.timedelta(days=1):
                dt = dt.replace(year=now.year - 1)
            return dt
        except Exception:
            return None
    return None

def scrape_ebay_item_specs(item_id, browser):
    """eBay詳細からスペック抽出"""
    url = f"https://www.ebay.com/itm/{item_id}"
    print(f"[*] eBay詳細読込中: {item_id}...", flush=True)
    try:
        tab = browser.new_tab(url)
        tab.get(url, timeout=15)
        handle_ebay_popups(tab)
        print(f"[*] HTML解析中...", flush=True)
        soup = BeautifulSoup(tab.html, 'html.parser')
        specs = {"weight": "不明", "dimensions": "不明"}
        spec_items = soup.find_all('div', class_='ux-labels-values__labels')
        for label_div in spec_items:
            label_text = label_div.get_text(strip=True).lower()
            value_div = label_div.find_next_sibling('div', class_='ux-labels-values__values')
            if not value_div: continue
            val = value_div.get_text(strip=True)
            if "weight" in label_text: specs["weight"] = val
            elif "dimensions" in label_text or "size" in label_text: specs["dimensions"] = val
        tab.close()
        print(f" -> スペック取得完了。", flush=True)
        return specs
    except Exception as e:
        print(f"[!] 詳細パース失敗: {e}", flush=True)
        return {"weight": "不明", "dimensions": "不明"}

def scrape_ebay_newest_items(url, browser):
    import urllib.parse
    
    # URLに配送先強制パラメータを追加
    parsed_url = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed_url.query)
    
    # 既存のリスト形式から単一値に変換して上書き
    params = {k: v[0] for k, v in params.items()}
    
    if "ebay.com" in parsed_url.netloc:
        params["_fcid"] = "1"      # Country: US
        params["_stpos"] = "10001" # Zip: New York
    elif "ebay.co.uk" in parsed_url.netloc:
        params["_fcid"] = "3"      # Country: UK
        params["_stpos"] = "E1 6AN" # Zip: London
        
    new_query = urllib.parse.urlencode(params)
    url = urllib.parse.urlunparse(parsed_url._replace(query=new_query))
    
    print(f"[*] eBay一覧読込中 (Ship-to強制): {url}", flush=True)
    scraped_items = []
    unique_ids = set()
    max_pages = 5
    
    try:
        tab = browser.latest_tab
        tab.get(url, timeout=20)
        handle_ebay_popups(tab)
        
        for page_num in range(1, max_pages + 1):
            print(f"[*] eBay一覧 {page_num}ページ目を解析中...", flush=True)
            tab.wait(2)
            tab.scroll.down(1500)
            tab.wait(1)
            soup = BeautifulSoup(tab.html, 'html.parser')
            
            # =======================================================
            # 修正: 's-item' クラス依存をやめ、確実な '/itm/' リンクベースで探索
            # =======================================================
            itm_links = soup.find_all('a', href=re.compile(r'/itm/'))
            
            now = datetime.datetime.now()
            # 1-15日なら当月1日以降、16-末日なら当月16日以降
            limit_date = datetime.datetime(now.year, now.month, 1) if now.day <= 15 else datetime.datetime(now.year, now.month, 16)
            
            items_on_this_page = 0
            found_older_item_on_this_page = False
            
            for link_tag in itm_links:
                try:
                    url = link_tag.get('href', '')
                    # タイトル付きURLにも対応した正規表現
                    item_id_match = re.search(r'/itm/(?:.*?/)?(\d{9,})', url)
                    if not item_id_match: continue
                    item_id = item_id_match.group(1)
                    
                    if item_id in unique_ids: continue
                    unique_ids.add(item_id)
                    
                    # リンクの親要素を遡って、商品ブロック全体（コンテナ）を特定する
                    container = link_tag
                    for _ in range(10):
                        container = container.parent
                        if not container: break
                        if container.name == 'li' or (container.name == 'div' and container.get('class') and any('item' in c.lower() for c in container.get('class'))):
                            break
                    
                    if not container: continue
                    
                    # 日付の取得: 商品カード全体のテキストから探すとセラー情報などを誤爆するため、
                    # まずは特定のクラス (listing-date) を探し、なければ全体から探す。
                    date_tag = container.find(['span', 'div'], class_=re.compile(r'listing-date|location', re.I))
                    date_text = date_tag.get_text(strip=True) if date_tag else container.get_text(" ", strip=True)
                    listing_date = parse_ebay_date(date_text)
                    
                    # 「New Listing」などの文字があれば現在時刻として扱う
                    if not listing_date and "new listing" in date_text.lower():
                        listing_date = now

                    # リミット日より古い商品が見つかったら、このページで終わり
                    if listing_date and listing_date < limit_date:
                        print(f"    [DEBUG] 期間外の商品を検出: {listing_date} < {limit_date} (Text: {date_text[:30]}...)")
                        found_older_item_on_this_page = True
                        continue
                        
                    title_tag = container.find(['h3', 'div', 'span'], class_=re.compile(r'title', re.I))
                    raw_title = title_tag.get_text(strip=True) if title_tag else "Unknown"
                    title = re.sub(r'Opens in a new window or tab|Opens in a new window|New Listing', '', raw_title, flags=re.IGNORECASE).strip()
                    
                    if "Shop on eBay" in title or not title or len(title) < 5: 
                        continue
                    
                    img_tag = container.find('img')
                    img_url = ""
                    if img_tag:
                        img_url = img_tag.get('data-src') or img_tag.get('src') or ""
                        if img_url.startswith('data:image') and img_tag.get('data-src'):
                            img_url = img_tag.get('data-src')

                    scraped_items.append({
                        "id": item_id, "title": title, "link": url, 
                        "image_url": img_url,
                        "timestamp": listing_date if listing_date else now
                    })
                    items_on_this_page += 1
                except: continue
            
            print(f"  -> {page_num}ページ目: {items_on_this_page} 件の新規商品を検出しました。", flush=True)
            
            # 古い商品が見つかったら終了
            if found_older_item_on_this_page:
                print(f"[*] 指定期間外の商品に到達したため、検索を終了します。", flush=True)
                break
            
            # 次のページボタンを探す
            next_btn = tab.ele('css:a.pagination__next', timeout=2)
            if next_btn:
                print(f"[*] 次のページへ遷移します...", flush=True)
                next_btn.click()
                tab.wait.load_start()
            else:
                print(f"[*] 次のページが見つかりません。検索を終了します。", flush=True)
                break
                
        print(f" -> 合計 {len(scraped_items)} 件の商品を検出しました。", flush=True)
        return scraped_items
    except Exception as e:
        print(f"[!] eBay一覧取得失敗: {e}", flush=True)
        return scraped_items # 途中でエラーが起きても取れた分は返す
