import os
import requests
import time
from config import GOOGLE_APPLICATION_CREDENTIALS

def search_by_google_lens(image_url, browser):
    """メインから渡されたブラウザを使用して Google Lens を実行する"""
    print(f"[*] Google Lens (ブラウザ版) で検索を開始します...", flush=True)
    results = []
    try:
        # 日本語結果を優先させるため lr=lang_ja と hl=ja を付与
        tab = browser.new_tab(f"https://www.google.com/searchbyimage?image_url={image_url}&client=app&lr=lang_ja&hl=ja")
        tab.wait.load_start()
        tab.wait(3) # レンダリング待ち
        
        # リンク一覧を抽出 (新セレクタ: a.LBcIee)
        tab.scroll.to_bottom() # 少しスクロールして読み込みを促す
        time.sleep(2)
        
        pref_domains = ["mercari.com", "rakuten.co.jp", "yahoo.co.jp", "shopping.yahoo.co.jp", "paypayfleamarket.yahoo.co.jp", "fril.jp", "amazon.co.jp"]
        items = tab.eles('css:a.LBcIee')
        for item in items:
            href = item.attr('href')
            if not href: continue
            
            # 国内ドメインに絞り込み
            if any(domain in href for domain in pref_domains):
                title_ele = item.ele('css:div[role="heading"]', timeout=1)
                text = title_ele.text.strip() if title_ele else item.text.strip()
                
                # 画像URL (サムネイル) を取得
                img_ele = item.ele('tag:img', timeout=1)
                img_url = img_ele.attr('src') if img_ele else ""
                
                if len(text) > 5:
                    results.append({"page_url": href, "title": text, "snippet": "", "img_url": img_url})
                
            if len(results) >= 15: break
        tab.close()
        print(f" -> Google Lens で国内サイトを {len(results)} 件抽出しました。", flush=True)
    except Exception as e:
        print(f"[!] Google Lens 失敗: {e}", flush=True)
    return results

def find_similar_images_on_web(image_uri, browser, max_results=15, force_lens=False):
    """APIを試行し、失敗または指定があれば Lens に切り替える（ブラウザを共有）"""
    # 0. 強制 Lens モード
    if force_lens:
        print("[*] Google Lens を強制実行します...", flush=True)
        return search_by_google_lens(image_uri, browser)

    # 1. API 試行
    if GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
        try:
            from google.cloud import vision
            client = vision.ImageAnnotatorClient()
            image = vision.Image()
            image.source.image_uri = image_uri
            
            # max_results=400 を指定するために AnnotateImageRequest を使用
            feature = vision.Feature(type_=vision.Feature.Type.WEB_DETECTION, max_results=400)
            request = vision.AnnotateImageRequest(image=image, features=[feature])
            response = client.batch_annotate_images(requests=[request]).responses[0]
            
            results = []
            if response.web_detection:
                # ページURLと画像URLの対応マップを作成
                url_to_img = {}
                # 完全一致と部分一致の画像からURLを拾う
                all_matching = list(response.web_detection.full_matching_images) + list(response.web_detection.partial_matching_images)
                for img in all_matching:
                    for page in img.url: # ここはページURLではなく画像のソースURL
                        pass # 実際には各ページがどの画像を持っているかの直接的な紐付けはAPIレベルでは難しいが、
                             # pages_with_matching_images の中にある程度含まれる

                # 国内ECサイト・マーケットプレイスを大幅に拡充
                pref_domains = [
                    "mercari.com", "rakuten.co.jp", "yahoo.co.jp", 
                    "shopping.yahoo.co.jp", "paypayfleamarket.yahoo.co.jp", 
                    "fril.jp", "borderless.store", "amazon.co.jp",
                    "shopping.geocities.jp", "paypaymall.yahoo.co.jp",
                    "buyee.jp", "item.rakuten.co.jp", "store.shopping.yahoo.co.jp",
                    "wowma.jp", "ponparemall.com", "yodobashi.com", "biccamera.com",
                    "kakaku.com", "suruga-ya.jp", "mandarake.co.jp", "mbok.jp",
                    "bookoffonline.co.jp", "netoff.co.jp", "hardoff.co.jp",
                    "lashinbang.com", "animate-onlineshop.jp", "amiami.jp",
                    "ec.line.me", "d-shopping.docomo.ne.jp", "qoo10.jp", "zara.com/jp"
                ]
                
                import re
                jp_pattern = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]')
                excluded_domains = {}
                excluded_examples = []
                domain_count = 0
                char_count = 0
                
                # Vision API では「どのページがどの画像URLを持っているか」の直接のリストはないが、
                # 検索に使った画像そのものが代表的な画像URLになるため、
                # fallback として ebay_img_url を使わずに各ドメインから画像を探すのが理想だが、
                # ここでは暫定的に全候補を返し、後の CLIP 判定で eBay 画像と比較する
                
                for page in response.web_detection.pages_with_matching_images:
                    url = page.url
                    title = page.page_title
                    is_pref_domain = any(domain in url for domain in pref_domains)
                    has_jp_chars = bool(jp_pattern.search(title))
                    
                    if is_pref_domain or has_jp_chars:
                        results.append({"page_url": url, "title": title, "snippet": "", "img_url": ""})
                        if is_pref_domain: domain_count += 1
                        else: char_count += 1
                    else:
                        domain = url.split('/')[2] if '//' in url else url.split('/')[0]
                        excluded_domains[domain] = excluded_domains.get(domain, 0) + 1
                        if len(excluded_examples) < 3: excluded_examples.append(url)
                
            if results:
                print(f"[*] Vision API で 400枚中 {len(results)} 件の国内候補を検出しました (ドメイン一致: {domain_count}, 日本語判定: {char_count})。", flush=True)
                return results[:max_results] 
            else:
                print("[*] Vision API (400件検索) で国内ドメインおよび日本語の結果が見つかりませんでした。", flush=True)
                if excluded_domains:
                    print(f"[*] 検出された主な海外/対象外ドメイン: {dict(list(sorted(excluded_domains.items(), key=lambda x: x[1], reverse=True))[:5])}", flush=True)
                    print(f"[*] 除外例: {excluded_examples}", flush=True)
        except Exception as e:
            print(f"[!] Vision API エラー (Lensに切り替えます): {e}", flush=True)

    # 2. API 0件またはエラーなら Google Lens を実行
    return search_by_google_lens(image_uri, browser)
