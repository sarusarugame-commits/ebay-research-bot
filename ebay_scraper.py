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
    print(f"[*] eBay一覧読込中...", flush=True)
    try:
        tab = browser.latest_tab
        tab.get(url, timeout=20)
        handle_ebay_popups(tab)
        tab.wait(3)
        tab.scroll.down(1500)
        soup = BeautifulSoup(tab.html, 'html.parser')
        itm_links = soup.find_all('a', href=re.compile(r'/itm/'))
        unique_ids = set()
        scraped_items = []
        now = datetime.datetime.now()
        limit_date = datetime.datetime(now.year, now.month, 1) if now.day <= 15 else datetime.datetime(now.year, now.month, 16)
        
        for link_tag in itm_links:
            try:
                link = link_tag.get('href')
                item_id = re.search(r'itm/(\d+)', link).group(1)
                if item_id in unique_ids: continue
                unique_ids.add(item_id)
                container = None
                p = link_tag
                for _ in range(10):
                    p = p.parent
                    if not p: break
                    if 's-item' in p.get('class', []) or 's-card' in p.get('class', []):
                        container = p; break
                if not container: continue
                listing_date = parse_ebay_date(container.get_text(separator=' ', strip=True))
                if listing_date and listing_date < limit_date: continue
                title_tag = container.find(['h3', 'div', 'span'], class_=re.compile(r'title', re.I))
                
                # 1. まずテキストを取得する
                raw_title = title_tag.get_text(strip=True) if title_tag else "Unknown"
                
                # 2. eBay特有のゴミ文字（アクセシビリティテキスト）を強制削除！
                title = raw_title.replace("Opens in a new window or tab", "").replace("Opens in a new window", "").strip()
                
                if "Shop on eBay" in title: continue
                img_tag = container.find('img', class_=re.compile(r'image', re.I)) or container.find('img')
                scraped_items.append({
                    "id": item_id, "title": title, "link": link, 
                    "image_url": img_tag.get('src') if img_tag else "",
                    "timestamp": listing_date if listing_date else now
                })
            except: continue
        print(f" -> {len(scraped_items)} 件の商品を検出しました。", flush=True)
        return scraped_items
    except Exception as e:
        print(f"[!] eBay一覧取得失敗: {e}", flush=True)
        return []
