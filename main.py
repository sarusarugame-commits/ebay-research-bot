import gpu_utils
import sys
BLUE  = "\033[94m"
RESET = "\033[0m"

import io
import re
import datetime
import traceback
import functools
import os
import requests

def global_exception_handler(exctype, value, tb):
    print("\n" + "="*50)
    print("!!! UNCAUGHT EXCEPTION !!!")
    traceback.print_exception(exctype, value, tb)
    print("="*50 + "\n")

sys.excepthook = global_exception_handler

# Windows環境でのエンコードエラー（CP932）対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
print = functools.partial(print, flush=True)

from config import EBAY_APP_ID, GOOGLE_APPLICATION_CREDENTIALS, RAKUTEN_APPLICATION_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID, YAHOO_CLIENT_ID
import database
from ebay_api import get_item_details
from mercari_scraper import search_mercari, search_rakuma, scrape_item_data
from surugaya_scraper import search_surugaya, scrape_surugaya_item
from llm_vision_judge import estimate_weight_with_llm, analyze_item_safety_and_tariff
from clip_judge_client import judge_similarity
# verify_with_lightglue は clip_judge 内で使われるようになったよ！
from ebay_scraper import scrape_ebay_newest_items, scrape_ebay_item_specs, get_browser_page
from vision_search import find_similar_images_on_web
from llm_namer import extract_product_name
from shopping_api import search_rakuten, search_yahoo, scrape_yahoo_item
from amazon_scraper import search_amazon, search_amazon_via_google, scrape_amazon_specs
from llm_vision_judge import verify_model_match
from excel_writer import write_to_sheet

def extract_specs_from_text(text):
    w, d = "不明", "不明"
    if not text: return w, d
    w_m = re.search(r"(\d+(\.\d+)?)\s?(kg|g|キロ|グラム)", text, re.I)
    if w_m: w = w_m.group(0)
    d_m = re.search(r"(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?(cm|mm|センチ|インチ|in)?", text, re.I)
    if d_m: d = d_m.group(0)
    return w, d

def adjust_dimensions(dims_str):
    if dims_str == "不明": return "不明"
    is_mm = "mm" in dims_str.lower()
    nums = re.findall(r"(\d+(\.\d+)?)", dims_str)
    if len(nums) >= 3:
        d1, d2, d3 = float(nums[0][0]), float(nums[1][0]), float(nums[2][0])
        # mmに統一
        if not is_mm:
            d1 *= 10; d2 *= 10; d3 *= 10
        # バッファ追加 (+20, +20, +10)
        d1, d2, d3 = int(d1 + 20), int(d2 + 20), int(d3 + 10)
        return f"{d1}x{d2}x{d3}mm"
    return dims_str

def truncate_weight(weight_str):
    if weight_str == "不明": return "不明"
    is_kg = "kg" in weight_str.lower() or "キロ" in weight_str
    nums = re.findall(r"(\d+(\.\d+)?)", weight_str)
    if nums:
        val = float(nums[0][0])
        # gに統一
        if is_kg:
            val *= 1000
        # バッファ追加 (+100g)
        val = int(val + 100)
        return f"{val}g"
    return weight_str

def save_debug_image(url, folder, filename):
    """画像をダウンロードして保存する（デバッグ用）"""
    try:
        if not os.path.exists(folder):
            os.makedirs(folder)
        # ファイル名のサニタイズ (改行やWindows禁止文字を除去)
        clean_filename = re.sub(r'[\r\n\t]', '', filename)
        clean_filename = re.sub(r'[\\/:*?"<>|]', '_', clean_filename)
        
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(os.path.join(folder, clean_filename), 'wb') as f:
                f.write(r.content)
    except Exception as e:
        print(f"    [DEBUG_IMG_ERROR] {e}")

def main():
    print("\n" + "="*50)
    print("   eBay/国内5大ECプラットフォーム 横断リサーチツール")
    print("="*50)
    
    url = input("eBayの検索結果URLを入力してください:\n> ").strip()
    if not url: return

    print("\n[*] ブラウザの起動を確認中...")
    browser = get_browser_page()
    if not browser:
        print("[!] ブラウザの取得に失敗したため終了します。")
        return
    print("[*] ブラウザ準備完了。")

    try:
        # 1. eBayスクレイピング
        print("\n[*] eBayから新着商品をスクレイピング中...")
        items = scrape_ebay_newest_items(url, browser)
        if not items: 
            print("[!] eBay商品が見つかりませんでした。")
            return
            
        items.sort(key=lambda x: x.get('timestamp', datetime.datetime.min), reverse=False)
        target_item = None
        for item in items:
            item_id_check = item.get('id')
            print(f"    [DB_CHECK] ID={item_id_check} -> is_researched={database.is_researched(item_id_check)}", flush=True)
            if database.is_researched(item_id_check):
                print(f"    [SKIP] 調査済み: {item_id_check} {item.get('title','')[:40]}")
            else:
                target_item = item
                break
        
        if not target_item:
            print("\n[OK] 指定されたURL内の全商品はすでにリサーチ済みです。")
            return
            
        item_id = target_item.get('id')
        print(f"\n" + "-"*50)
        print(f" 【リサーチ開始】 eBay商品 ID: {item_id}")
        print(f" タイトル: {target_item.get('title')}")
        print(f" 画像URL: {target_item.get('image_url')}")
        print("-" * 50)
        
        # 1.5 安全性・関税チェック (Gemma 3 Vision) - 複数画像対応
        ebay_specs_pre = scrape_ebay_item_specs(item_id, browser)
        all_img_urls = ebay_specs_pre.get("img_urls") or []
        if not all_img_urls:
            all_img_urls = [target_item.get('image_url')]
        print(f"\n[*] Gemma 3 が商品の安全性をチェックしています（アルコール/高関税素材）... ({len(all_img_urls)}枚)")
        safety_data = analyze_item_safety_and_tariff(all_img_urls[0], all_img_urls)
        
        # 判定結果の表示
        print(f"    - アルコール判定: {'あり (⚠️ SKIP)' if safety_data.get('is_alcohol') else 'なし'}")
        print(f"    - 高関税素材判定: {'あり (⚠️ ATTENTION)' if safety_data.get('is_high_tariff') else 'なし'}")
        if safety_data.get('label'):
            print(f"    - 検出された素材: {safety_data.get('label')}")

        if safety_data.get("is_alcohol"):
            print(f"\n[⚠️ SKIP] アルコール飲料が検出されました。ガイドラインによりこの商品はスキップします。")
            database.mark_as_researched(item_id, weight="SKIPPED", dimensions="ALCOHOL")
            return

        high_tariff_flag = safety_data.get("is_high_tariff", False)
        material_label = safety_data.get("label", "なし")
        if high_tariff_flag:
            print(f"\n[⚠️ ATTENTION] 高関税対象素材（{material_label}）の可能性があります。")
        
        # スペック収集（安全性チェック時に取得済みのものを再利用）
        ebay_specs = ebay_specs_pre
        
        raw_w, raw_d = ebay_specs.get("weight", "不明"), ebay_specs.get("dimensions", "不明")
        # Geminiが選んだ最良画像を使用（なければ先頭画像）
        img_url = safety_data.get("best_img_url") or all_img_urls[0] if all_img_urls else target_item.get("image_url")

        # 2. 画像検索
        print("\n[*] Google Vision API / Lens を使用して類似画像を検索中...")
        candidate_pages = find_similar_images_on_web(img_url, browser, max_results=5)
        
        scored_candidates = []
        if candidate_pages:
            # Vision API / Lens でドメインフィルター済みなので、そのまま使う
            scored_candidates = candidate_pages[:5]

            if not scored_candidates:
                print("    [!] 国内の候補が全く見つかりませんでした。")

            # 寸法（dimensions）が不明な場合はテキストから推測する処理
            if raw_d == "不明":
                for p in scored_candidates:
                    _, d = extract_specs_from_text(p.get("title", "") + " " + p.get("snippet", ""))
                    if d != "不明": raw_d = d; break



        # 3. 商品名特定
        print("\n[*] AI を使用して日本語正式商品名を特定中...")
        name_data = extract_product_name(target_item.get('title'), scored_candidates)
        final_name = name_data.get("full_name", "特定不能")
        print(f" -> 最終確定した日本語名: {final_name}")

        # ==========================================
        # 【新規】英語商品名を特定する独立プロセス！ (Vision API版)
        # ==========================================
        print("\n[*] VisionAPI(Google Lens)経由で英語商品名（正確な型番）を特定中...")
        from vision_search import search_global_images_by_lens
        from llm_namer import extract_english_product_name
        
        final_en_name = target_item.get('title') # 初期値
        
        # 1. Vision API のみで海外候補を取得（Lens補填なしで高速化）
        from vision_search import search_global_images_by_lens as _search_global
        from vision_search import find_similar_images_on_web as _find_similar
        # Vision APIだけで取得（force_lens=Falseかつmax_results=5で補填なし）
        en_candidates = _search_global(img_url, browser, max_results=5)
        
        if en_candidates:
            print(f"    [*] 取得した {len(en_candidates)} 件の海外候補から英語名を抽出中...")
            en_name_data = extract_english_product_name(target_item.get('title'), en_candidates[:5])
            final_en_name = en_name_data.get("full_name", target_item.get('title'))
        else:
            print("    [!] 海外候補が取得できませんでした。元のタイトルを使用します。")
            
        print(f" -> 最終確定した英語名: {final_en_name}")
        # ==========================================

        # デバッグ画像用フォルダの作成
        debug_folder = f"debug_images/{item_id}_{datetime.datetime.now().strftime('%H%M%S')}"
        save_debug_image(img_url, debug_folder, "00_ebay_reference.jpg")

        # 4. 国内横断検索 (2段階フェーズ)
        final_candidates = []
        weight_final = "不明"
        dims_final = "不明"
        tentative_best_item = None
        tentative_best_price = float('inf')

        if final_name and final_name != "特定不能":
            # 検索用クエリの作成 (ブランド + 型番) を優先
            # brand はLLMが誤変換することがあるため series+model+keywords を使う
            series_model = f"{name_data.get('series', '')} {name_data.get('model', '')} {name_data.get('keywords', '')}".strip().replace('  ', ' ')
            search_query = series_model if len(series_model) > 5 else final_name
            
            # 共通のフィルタリング用キーワード
            filter_text = f"{name_data.get('series', '')} {name_data.get('model', '')} {name_data.get('keywords', '')}".strip()
            import unicodedata
            def normalize_text(text):
                return unicodedata.normalize('NFKC', text).lower()

            search_keywords = [normalize_text(k) for k in re.split(r'\s+', filter_text) if len(k) > 1]
    # 全角スペースを含む空白文字全般で分割するよう \s に統一
            model_name = normalize_text(name_data.get('model', ''))
            series_name = normalize_text(name_data.get('series', ''))

            def process_candidates(candidates, platform):
                nonlocal tentative_best_price, tentative_best_item, weight_final, dims_final
                
                if not candidates:
                    print(f"[*] {platform}: 検索結果 0 件でした。")
                    return

                print(f"[*] {platform}: {len(candidates)} 件の候補を詳細判定中 (基準: 加重平均 73%以上)")
                
                # 1. 商品名・型番判定 (厳格化)
                keyword_filtered = []
                for item in candidates:
                    title_norm = normalize_text(item.get("title", ""))
                    
                    # 型番または商品名（シリーズ名）が含まれているかをチェック
                    # 型番は3文字以上、シリーズ名は2文字以上を有効とする
                    has_model = (model_name in title_norm) if len(model_name) >= 3 else False
                    has_series = (series_name in title_norm) if len(series_name) >= 2 else False
                    
                    if has_model or has_series:
                        keyword_filtered.append(item)
                    else:
                        reasons = []
                        if len(model_name) >= 3: reasons.append(f"型番({model_name})不一致")
                        if len(series_name) >= 2: reasons.append(f"商品名({series_name})不一致")
                        reason_str = " / ".join(reasons) if reasons else "特定識別子なし"
                        print(f"    [SKIP] {reason_str}: {item.get('title')[:30]}...")
                
                if not keyword_filtered:
                    print(f"    [!] キーワード条件に満たないため、全件を画像判定へ回します。")
                    keyword_filtered = candidates

                browser_page = get_browser_page() # 詳細スクレイピング用

                for item in keyword_filtered:
                    item_title = item.get('title', '不明')
                    page_url = item.get('page_url', '')
                    
                    if not page_url or not page_url.startswith("http"):
                        print(f"    [SKIP] 無効なURL: {item_title[:20]} (URL: {page_url})")
                        continue

                    print(f"    [*] 候補精査: {item_title[:30]}")
                    print(f"           URL: {BLUE}{page_url}{RESET}")
                    
                    # 2. 詳細情報の取得 (画像5枚を取得するため)
                    detail = None
                    if platform in ["メルカリ", "ラクマ"]:
                        detail = scrape_item_data(page_url, browser_page)
                    elif platform == "駿河屋":
                        detail = scrape_surugaya_item(page_url, browser_page)
                    elif platform == "Yahooショッピング":
                        detail = scrape_yahoo_item(page_url, browser_page)
                    
                    if detail:
                        item.update(detail) # img_urls 等を更新
                    
                    # img_urls がない場合は一覧の img_url を使う
                    current_img_urls = item.get("img_urls", [])
                    if not current_img_urls and item.get("img_url"):
                        current_img_urls = [item["img_url"]]
                    
                    if not current_img_urls:
                        print(f"    [SKIP] 画像が見つかりません: {item_title[:30]}")
                        continue

                    # 3. 画像判定 (DINOv2) - 最大5枚すべてチェック
                    print(f"    [*] 画像判定中 ({len(current_img_urls)}枚): {item_title[:20]}...")
                    
                    img_candidates = [{"img_url": u, "page_url": page_url} for u in current_img_urls]
                    judged_imgs = judge_similarity(img_url, img_candidates)
                    
                    if not judged_imgs:
                        print(f"    [REJECT] 画像判定エラー")
                        continue

                    # ベストスコアのものを採用
                    best_ji = judged_imgs[0]
                    final_score = float(best_ji.get("score", 0))
                    
                    # デバッグ画像の保存
                    for i, ji in enumerate(judged_imgs[:3]):
                        s = float(ji.get("score", 0))
                        img_filename = f"{platform}_{item_title[:10]}_{i}_score_{s:.1f}.jpg".replace("/", "_").replace(" ", "_")
                        save_debug_image(ji.get("img_url"), debug_folder, img_filename)

                    if final_score <= 0:
                        print(f"    [REJECT] 類似度スコア0（model_serverで除外済み）")
                        continue

                    print(f"    [MATCH] 検証合格 ({final_score:.1f}%) URL: {BLUE}{page_url}{RESET}")

                    # 5. 送料計算と最安値更新
                    detail_price_str = str(item.get('price', '0'))
                    p_str = re.sub(r'[^\d]', '', detail_price_str)
                    price_val = int(p_str) if p_str else 0
                    
                    item["score"] = final_score
                    item["price_int"] = price_val
                    ship_fee = item.get("actual_shipping_fee", 0)
                    total_price = item["price_int"] + ship_fee
                    item["total_price"] = total_price
                    
                    print(f"       -> 価格(送料込): ¥{total_price:,} (本体:¥{item['price_int']:,} + 送料:¥{ship_fee:,}) / 状態: {item.get('condition', '不明')}")

                    if total_price < tentative_best_price:
                        tentative_best_price = total_price
                        tentative_best_item = item
                        print(f"       >>> 暫定最安値を更新しました！ (¥{total_price:,})")

                    if weight_final == "不明" or dims_final == "不明":
                        desc_text = item.get("description", "") + " " + item.get("title", "")
                        ext_w, ext_d = extract_specs_from_text(desc_text)
                        if weight_final == "不明" and ext_w != "不明": weight_final = ext_w
                        if dims_final == "不明" and ext_d != "不明": dims_final = ext_d
                    
                    final_candidates.append(item)

        def get_fresh_browser():
            nonlocal browser
            try:
                if not browser:
                    print("    [*] ブラウザを新規起動します...")
                    browser = get_browser_page()
                else:
                    print("    [*] ブラウザの生存確認中...", end=" ", flush=True)
                    _ = browser.latest_tab
                    print("OK")
            except Exception:
                print("[!] ブラウザの状態異常を検知したため、再起動します...")
                browser = get_browser_page()
            return browser

        if final_name and final_name != "特定不能":
            # brand はLLMが誤変換することがあるため series+model+keywords を使う
            series_model = f"{name_data.get('series', '')} {name_data.get('model', '')} {name_data.get('keywords', '')}".strip().replace('  ', ' ')
            search_query = series_model if len(series_model) > 5 else final_name
            
            filter_text = f"{name_data.get('series', '')} {name_data.get('model', '')} {name_data.get('keywords', '')}".strip()
            import unicodedata
            def normalize_text(text):
                return unicodedata.normalize('NFKC', text).lower()

            search_keywords = [normalize_text(k) for k in re.split(r'\s+', filter_text) if len(k) > 1]
            model_name = normalize_text(name_data.get('model', ''))
            series_name = normalize_text(name_data.get('series', ''))

            print(f"\n[*] 国内5大プラットフォームを順次調査開始...")
            
            def log_search(platform):
                print(f"    [🔍] {platform} を検索中...")

            # メルカリ
            browser = get_fresh_browser()
            log_search("メルカリ")
            m_res = search_mercari(search_query, browser, max_results=15)
            process_candidates(m_res, "メルカリ")

            # ラクマ
            browser = get_fresh_browser()
            log_search("ラクマ")
            r_res = search_rakuma(search_query, browser, max_results=10)
            process_candidates(r_res, "ラクマ")

            # 駿河屋
            browser = get_fresh_browser()
            log_search("駿河屋")
            s_res = search_surugaya(search_query, browser, max_results=10)
            process_candidates(s_res, "駿河屋")

            # 楽天・Yahoo APIは長いクエリでヒットしにくいのでシリーズ+型番+日本語名に絞る
            from shopping_api import search_rakuten
            _series = name_data.get('series', '')
            _model = name_data.get('model', '')
            _jp_name = final_name.replace(_series, '').replace(_model, '').strip(' ,、')
            api_query = f"{_series} {_model} {_jp_name}".strip() or search_query
            rakuten_items = search_rakuten(api_query)
            process_candidates(rakuten_items, "楽天市場")

            from shopping_api import search_yahoo
            yahoo_items = search_yahoo(api_query)
            process_candidates(yahoo_items, "Yahooショッピング")

        if weight_final == "不明" and raw_w != "不明": weight_final = truncate_weight(raw_w)
        if dims_final == "不明" and raw_d != "不明": dims_final = adjust_dimensions(raw_d)

        # 5. 【最終補完】スペック情報（重量/サイズ）の補完
        if weight_final == "不明" or dims_final == "不明":
            # --- STEP 5.1: Amazon からの補完 ---
            print("\n[*] 重量またはサイズが不明なため、Amazonからスペック情報を補完中...")
            try:
                browser = get_fresh_browser()
                amz_search_query = final_name if final_name != "特定不能" else target_item.get('title')
                amz_results = search_amazon(amz_search_query, browser, max_results=5)
                
                # 1. Amazon内検索でヒットした場合（画像あり）
                if amz_results:
                    amz_for_judge = [{"img_url": r.get("img_url", ""), "page_url": r.get("page_url", ""), "_orig": r} 
                                     for r in amz_results if r.get("img_url")]
                    amz_judged = judge_similarity(img_url, amz_for_judge)
                    
                    if amz_judged and amz_judged[0] is not None and amz_judged[0].get("score", 0) >= 70:
                        best_amz = amz_judged[0]
                        amz_url = best_amz.get("page_url") or best_amz.get("_orig", {}).get("page_url")
                        if amz_url:
                            print(f"    [MATCH] Amazonで一致商品を発見 (Score: {best_amz['score']:.1f}%).")
                            amz_specs = scrape_amazon_specs(amz_url, browser)
                            if weight_final == "不明" and amz_specs.get("weight") != "不明":
                                weight_final = truncate_weight(amz_specs["weight"])
                            if dims_final == "不明" and amz_specs.get("dimensions") != "不明":
                                dims_final = adjust_dimensions(amz_specs["dimensions"])
                
                # 2. ヒットせず、Google経由で検索した場合（画像なし）
                else:
                    print("    [!] Amazon内検索でヒットしないため、Google経由でAmazonを検索します...")
                    amz_google_results = search_amazon_via_google(amz_search_query, browser, max_results=3)
                    
                    if amz_google_results:
                        for r in amz_google_results:
                            amz_url = r.get("page_url")
                            print(f"    [*] Google経由で発見したページを解析中: {amz_url}")
                            
                            # --- 🌟 追加: Amazon詳細ページを開いて画像を直接取得し、判定する ---
                            tab = browser.latest_tab
                            tab.get(amz_url)
                            tab.wait(2)
                            
                            # 画像要素の取得 (Amazonの商品画像は主に #landingImage か #imgBlkFront)
                            img_ele = tab.ele('#landingImage') or tab.ele('#imgBlkFront')
                            if img_ele:
                                amz_img_src = img_ele.attr('src') or img_ele.attr('data-old-hires')
                                if amz_img_src:
                                    print("    [*] 詳細ページから画像を取得。類似度判定を実行します...")
                                    amz_judged = judge_similarity(img_url, [{"img_url": amz_img_src, "page_url": amz_url}])
                                    if not amz_judged or amz_judged[0] is None or amz_judged[0].get("score", 0) < 70:
                                        score = amz_judged[0].get("score", 0) if (amz_judged and amz_judged[0]) else 0
                                        print(f"    [SKIP] 画像判定不合格 (Score: {score:.1f}%)")
                                        continue
                                    print(f"    [MATCH] 画像判定合格！スペックを抽出します...")
                            # --------------------------------------------------------
                            
                            # 判定に合格した場合（または画像が取れなかった場合）にスペック抽出
                            amz_specs = scrape_amazon_specs(amz_url, browser)
                            
                            found_any = False
                            if weight_final == "不明" and amz_specs.get("weight") != "不明":
                                weight_final = truncate_weight(amz_specs["weight"])
                                found_any = True
                            if dims_final == "不明" and amz_specs.get("dimensions") != "不明":
                                dims_final = adjust_dimensions(amz_specs["dimensions"])
                                found_any = True
                                
                            if found_any:
                                print("    [MATCH] Google経由のAmazonページからスペックの補完に成功しました。")
                                break
                                
            except Exception as e:
                print(f"    [!] Amazonスペック補完中にエラー: {e}")

            # --- STEP 5.2: Amazonでも不明な場合の LLM(Gemma) 推定 ---
            if weight_final == "不明" or dims_final == "不明":
                print("\n[*] Amazonでも判明しなかったため、LLM(画像)による重量・サイズ推定を実行します...")
                try:
                    llm_estimated = estimate_weight_with_llm(img_url, final_name)
                    if llm_estimated:
                        if weight_final == "不明": weight_final = llm_estimated.get("weight", "不明")
                        if dims_final == "不明": dims_final = llm_estimated.get("dimensions", "不明")
                except Exception as e:
                    print(f"    [!] LLM推定処理でエラー: {e}")

        best_item = tentative_best_item

        # ===== 国内最安値のLLM型番検証（同一性＋コンディション同時判定） =====
        ebay_condition = "Good" # デフォルト
        if best_item and name_data.get("model"):
            model_number = name_data.get("model", "")
            print(f"\n[*] 国内最安値商品をLLMで詳細検証中（型番: {model_number}）...")

            sorted_candidates = sorted(final_candidates, key=lambda x: x.get("total_price", float('inf')))

            best_item = None
            for cand in sorted_candidates:
                cand_img = cand.get("img_urls", [None])[0] or cand.get("img_url")

                if not cand_img:
                    print(f"    [LLM] 画像URLなし → 通過扱い: {cand.get('title','')[:30]}")
                    best_item = cand
                    break

                # 同一性とコンディションを同時に判定
                domestic_desc = cand.get("condition", "") + " " + cand.get("description", "")
                is_match, cond = verify_model_match(img_url, cand_img, model_number, domestic_desc)
                
                if is_match:
                    best_item = cand
                    ebay_condition = cond
                    # あとでExcel書き込みに使うために保存しておく
                    best_item['ebay_condition'] = ebay_condition
                    break
                else:
                    # 不一致の場合は次点へ
                    pass

            if not best_item:
                print("    [!] LLM検証で全候補が除外されました。最安値なしとして処理します。")
        # ====================================================================
        if best_item:
            database.mark_as_researched(
                item_id, 
                platform=best_item.get("platform"), 
                title=best_item.get("title"), 
                price=best_item.get("total_price"), 
                condition=best_item.get("condition"), 
                url=best_item.get("page_url"), 
                weight=weight_final, 
                dimensions=dims_final
            )
            
            
            print("\n[*] 国内最安値が判明したため、eBay全体での競合最安値（US/UK）をチェックします...")

            print("\n[*] 国内最安値が判明したため、eBay全体での競合最安値（US/UK）をチェックします...")
            from validate_ebay_search_v3 import process_market, get_ebay_token
            
            token = get_ebay_token()
            if token:
                print(f"[*] eBay競合検索キーワード: {final_en_name}")
                ebay_model_number = name_data.get("model", "")
                
                # 日本で見つかった最安値商品のコンディションを判別
                best_cond_str = str(best_item.get("condition", "")).lower()
                # 1. 「新品」「new」が含まれているか判定
                contains_new = "新品" in best_cond_str or "new" in best_cond_str
                # 2. 中古を示唆するキーワードが含まれているか判定（「同様」「展示」などは新古品＝USED扱い）
                is_used_keyword = any(k in best_cond_str for k in ["中古", "used", "2", "傷", "汚れ", "近い", "同様", "訳あり", "展示", "ランク"])
                # 3. 「未開封」があれば新品扱いを優先
                is_unopened = "未開封" in best_cond_str
                
                if is_unopened:
                    target_cond = "NEW"
                elif contains_new and not is_used_keyword:
                    target_cond = "NEW"
                else:
                    target_cond = "USED"
                
                print(f"    [*] 国内最安値のコンディション: {best_item.get('condition')} -> eBay調査条件: {target_cond}")
                
                # US検索
                us_top3 = process_market(token, "EBAY_US", final_en_name, img_url, target_cond, model_number=ebay_model_number)
                # フォールバック: 新品で探していたが、結果が0件だった場合は中古で再試行
                if target_cond == "NEW" and not us_top3:
                    print(f"    [!] USで新品が見つからないため、中古で再検索します...")
                    us_top3 = process_market(token, "EBAY_US", final_en_name, img_url, "USED", model_number=ebay_model_number)
                
                # UK検索
                uk_top3 = process_market(token, "EBAY_GB", final_en_name, img_url, target_cond, model_number=ebay_model_number)
                # フォールバック
                if target_cond == "NEW" and not uk_top3:
                    print(f"    [!] UKで新品が見つからないため、中古で再検索します...")
                    uk_top3 = process_market(token, "EBAY_GB", final_en_name, img_url, "USED", model_number=ebay_model_number)

                # ミラーリングロジック
                if us_top3 and not uk_top3:
                    print("[*] UK の結果が空のため、US の結果を UK にミラーリングします。")
                    uk_top3 = us_top3.copy()
                elif uk_top3 and not us_top3:
                    print("[*] US の結果が空のため、UK の結果を US にミラーリングします。")
                    us_top3 = uk_top3.copy()

                print("\n" + "="*50)
                print("   🏆 eBay Global 競合最安値 (Top 3)")
                print("="*50)
                results = {"US": us_top3, "UK": uk_top3}
                for m_id in ["US", "UK"]:
                    print(f"\n[Market: eBay {m_id}]")
                    if not results[m_id]:
                        print("  一致する商品は見つかりませんでした。")
                    else:
                        for i, res in enumerate(results[m_id], 1):
                            print(f"  Rank {i}: {res['title'][:60]}...")
                            print(f"    - 合計価格: ${res['total_usd']:,.2f} USD (本体:{res['price']} {res['currency']} + 送料:{res['shipping']})")
                            print(f"    - 適合率:   {res['score']:.1f}%")
                            print(f"    - URL:      {BLUE}{res['item_url']}{RESET}")
            else:
                print("[!] eBay APIトークンが取得できなかったため、競合チェックをスキップします。")
        # 6. Excelへの自動書き込み
        if best_item:
            # 書き込み用データの整形
            def ext_dim(d_str, idx):
                nums = re.findall(r"\d+", d_str)
                return nums[idx] if len(nums) > idx else ""

            def ext_weight(w_str):
                match = re.search(r"\d+", w_str.replace(",", ""))
                return match.group() if match else ""

            # 保存された ebay_condition を取得
            final_ebay_condition = best_item.get('ebay_condition', ebay_condition)

            item_data = {
                "product_name": final_name,
                "length": ext_dim(dims_final, 0),
                "width": ext_dim(dims_final, 1),
                "height": ext_dim(dims_final, 2),
                "weight": ext_weight(weight_final),
                "domestic_price": best_item.get("total_price", 0),
                "us_top3_prices": [item["total_usd"] for item in us_top3] if 'us_top3' in locals() else [],
                "uk_top3_prices": [item["total_usd"] for item in uk_top3] if 'uk_top3' in locals() else [],
                "us_shipping_jpy": locals().get('calculated_us_shipping_jpy', 0),
                "uk_shipping_jpy": locals().get('calculated_uk_shipping_jpy', 0),
                "source_url": best_item.get("page_url", ""),
                "is_high_tariff": high_tariff_flag,
                "condition": final_ebay_condition
            }

            write_to_sheet(item_data)
        else:
            print("\n[!] 国内最安値が見つからなかったため、Excelへの書き込みをスキップします。")

        print(f"{BLUE}\n{'='*60}{RESET}")
        print(f"{BLUE}                 ✨ リサーチ完了 ✨{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}■ eBay商品 ID  : {item_id}{RESET}")
        print(f"{BLUE}■ 判定した商品名: {final_name}{RESET}")
        print(f"{BLUE}{'-' * 60}{RESET}")
        
        if best_item:
            print(f"{BLUE}【！国内最安値確定商品！】{RESET}")
            print(f"{BLUE}■ プラットフォーム: {best_item.get('platform')}{RESET}")
            print(f"{BLUE}■ 商品タイトル    : {best_item.get('title')}{RESET}")
            print(f"{BLUE}■ 送料込み価格    : ¥{best_item.get('total_price', 0):,}{RESET}")
            print(f"{BLUE}■ 商品URL         : {best_item.get('page_url')}{RESET}")
            print(f"{BLUE}■ 加重平均スコア  : {best_item.get('score', 0):.1f}%{RESET}")
            print(f"{BLUE}■ 商品の状態      : {best_item.get('condition')}{RESET}")
        else:
            print(f"{BLUE} domestic lowest price NOT found (類似の商品が見つかりませんでした)。{RESET}")
        
        if high_tariff_flag:
            print(f"{BLUE}\n【⚠️ 重要：高関税注意】{RESET}")
            print(f"{BLUE}この商品は AI により「{material_label}」素材である可能性が高いと判定されました。{RESET}")
            print(f"{BLUE}鉄・鋼鉄製品や本革製品は関税が高くなる可能性があるため、仕入れ前に再確認してください。{RESET}")
            
        print(f"{BLUE}{'-' * 60}{RESET}")
        print(f"{BLUE}■ 推定重量        : {weight_final}{RESET}")
        print(f"{BLUE}■ 推定サイズ      : {dims_final}{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}[*] データベースへの記録が完了しました。\n{RESET}")

    except Exception as e:
        print(f"\n[！重大なエラーが発生しました！]")
        print(f"エラー内容: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
