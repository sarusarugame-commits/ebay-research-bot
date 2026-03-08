import gpu_utils
import sys
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

from config import EBAY_APP_ID, GOOGLE_APPLICATION_CREDENTIALS
import database
from ebay_api import get_item_details
from mercari_scraper import search_mercari, search_rakuma, scrape_item_data
from surugaya_scraper import search_surugaya, scrape_surugaya_item
from llm_vision_judge import estimate_weight_with_llm, analyze_item_safety_and_tariff
from clip_judge import judge_similarity
# verify_with_lightglue は clip_judge 内で使われるようになったよ！
from ebay_scraper import scrape_ebay_newest_items, scrape_ebay_item_specs, get_browser_page
from vision_search import find_similar_images_on_web
from llm_namer import extract_product_name
from amazon_scraper import search_amazon, scrape_amazon_specs

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
    nums = re.findall(r"(\d+(\.\d+)?)", dims_str)
    if len(nums) >= 3:
        d1, d2, d3 = int(float(nums[0][0]) + 2), int(float(nums[1][0]) + 2), int(float(nums[2][0]) + 1)
        unit = "mm" if "mm" in dims_str.lower() else "cm"
        return f"{d1}x{d2}x{d3} {unit}"
    return dims_str

def truncate_weight(weight_str):
    if weight_str == "不明": return "不明"
    nums = re.findall(r"(\d+(\.\d+)?)", weight_str)
    if nums:
        val = int(float(nums[0][0]))
        unit = "kg" if "kg" in weight_str.lower() or "キロ" in weight_str else "g"
        return f"{val}{unit}"
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
        target_item = items[0] if items else None
        # for item in items:
        #     if not database.is_researched(item.get('id')):
        #         target_item = item; break
        
        if not target_item:
            print("\n[OK] 指定されたURL内の全商品はすでにリサーチ済みです。")
            return
            
        item_id = target_item.get('id')
        print(f"\n" + "-"*50)
        print(f" 【リサーチ開始】 eBay商品 ID: {item_id}")
        print(f" タイトル: {target_item.get('title')}")
        print(f" 画像URL: {target_item.get('image_url')}")
        print("-" * 50)
        
        # 1.5 安全性・関税チェック (Gemma 3 Vision)
        print("\n[*] Gemma 3 が商品の安全性をチェックしています（アルコール/高関税素材）...")
        safety_data = analyze_item_safety_and_tariff(target_item.get('image_url'))
        
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
        
        # スペック収集
        ebay_specs = scrape_ebay_item_specs(item_id, browser)
        
        raw_w, raw_d = ebay_specs.get("weight", "不明"), ebay_specs.get("dimensions", "不明")
        img_url = target_item.get("image_url")

        # 2. 画像検索
        print("\n[*] Google Vision API / Lens を使用して類似画像を検索中...")
        candidate_pages = find_similar_images_on_web(img_url, browser, max_results=5)
        
        scored_candidates = []
        if candidate_pages:
            jp_pattern = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]')
            jp_candidates = []
            
            # まずはVision APIの結果から日本語候補を抽出
            for p in candidate_pages:
                text = (p.get('title', '') + ' ' + p.get('snippet', '')).strip()
                if jp_pattern.search(text):
                    jp_candidates.append(p)
            
            # 日本語候補を抽出 (すでに行われているはずだが、念のため正規化)
            scored_candidates = jp_candidates[:5]

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
        
        # 1. VisionAPI (Google Lens) で海外の画像候補を取得
        en_candidates = search_global_images_by_lens(img_url, browser)
        
        if en_candidates:
            # 司令官の指示により、画像類似判定（DINO/Color）は行わず、取得したタイトル群から直接AIが抽出します
            print(f"    [*] 取得した {len(en_candidates)} 件の海外候補から英語名を抽出中...")
            # 上位5件程度の候補をAIに渡す
            top_en_matches = en_candidates[:5]
            
            en_name_data = extract_english_product_name(target_item.get('title'), top_en_matches)
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
            brand_model = f"{name_data.get('brand', '')} {name_data.get('model', '')}".strip()
            search_query = brand_model if len(brand_model) > 5 else final_name
            
            # 共通のフィルタリング用キーワード
            filter_text = f"{name_data.get('series', '')} {name_data.get('model', '')} {name_data.get('keywords', '')}".strip()
            import unicodedata
            def normalize_text(text):
                return unicodedata.normalize('NFKC', text).lower()

            search_keywords = [normalize_text(k) for k in re.split(r'[\s　]+', filter_text) if len(k) > 1]
            model_name = normalize_text(name_data.get('model', ''))

            def process_candidates(candidates, platform):
                nonlocal tentative_best_price, tentative_best_item, weight_final, dims_final
                
                if not candidates:
                    print(f"[*] {platform}: 検索結果 0 件でした。")
                    return

                print(f"[*] {platform}: {len(candidates)} 件の候補を詳細判定中 (基準: 加重平均 73%以上)")
                
                # 1. キーワードフィルタリング
                threshold = max(1, len(search_keywords) // 2)
                keyword_filtered = []
                for item in candidates:
                    title_norm = normalize_text(item.get("title", ""))
                    num_matches = sum(1 for k in search_keywords if k in title_norm)
                    has_model = model_name in title_norm if len(model_name) > 3 else False
                    
                    if has_model or num_matches >= threshold:
                        keyword_filtered.append(item)
                    else:
                        print(f"    [SKIP] キーワード不足 ({num_matches}/{threshold}): {item.get('title')[:30]}...")
                
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
                    print(f"           URL: {page_url}")
                    
                    # 2. 詳細情報の取得 (画像5枚を取得するため)
                    detail = None
                    if platform in ["メルカリ", "ラクマ"]:
                        detail = scrape_item_data(page_url, browser_page)
                    elif platform == "駿河屋":
                        detail = scrape_surugaya_item(page_url, browser_page)
                    
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

                    if final_score < 73:
                        print(f"    [REJECT] 最終加重スコア不足 ({final_score:.1f}%)")
                        continue

                    print(f"    [MATCH] 検証合格 ({final_score:.1f}%) URL: {page_url}")

                    # 5. 送料計算と最安値更新
                    detail_price_str = str(item.get('price', '0'))
                    p_str = re.sub(r'[^\d]', '', detail_price_str)
                    price_val = int(p_str) if p_str else 0
                    
                    if price_val <= 0:
                        print(f"    [SKIP] 不正な価格 (¥{price_val}): {item_title[:20]}")
                        continue

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
                    # 司令官流！ latest_tab に触れるかどうかで生存確認するよ！
                    if not browser:
                        print("    [*] ブラウザを新規起動します...")
                        browser = get_browser_page()
                    else:
                        print("    [*] ブラウザの生存確認中...", end=" ", flush=True)
                        _ = browser.latest_tab  # これでエラーが出なければブラウザは元気！
                        print("OK")
                except Exception:
                    print("[!] ブラウザの状態異常を検知したため、再起動します...")
                    browser = get_browser_page()
                return browser

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

            from shopping_api import search_rakuten
            rakuten_items = search_rakuten(search_query)
            process_candidates(rakuten_items, "楽天市場")

            from shopping_api import search_yahoo
            yahoo_items = search_yahoo(search_query)
            process_candidates(yahoo_items, "Yahooショッピング")

        if weight_final == "不明" and raw_w != "不明": weight_final = truncate_weight(raw_w)
        if dims_final == "不明" and raw_d != "不明": dims_final = adjust_dimensions(raw_d)

        # 5. 【最終補完】Amazonからのスペック抽出（重量/サイズが不明の場合のみ）
        if weight_final == "不明" or dims_final == "不明":
            print("\n[*] 重量またはサイズが不明なため、Amazonからスペック情報を最終補完中...")
            try:
                browser = get_fresh_browser()
                # 型番がない商品も考慮し、AIが特定した日本語の商品名（またはeBayタイトル）で検索
                amz_search_query = final_name if final_name != "特定不能" else target_item.get('title')
                print(f"    [*] Amazon検索クエリ: {amz_search_query}")
                amz_results = search_amazon(amz_search_query, browser, max_results=5)
                
                if amz_results:
                    # 画像判定で一致するか確認
                    print(f"    [*] Amazonで見つかった {len(amz_results)} 件の候補を画像判定中...")
                    amz_judged = judge_similarity(img_url, amz_results)
                    
                    if amz_judged and amz_judged[0].get("score", 0) >= 70:
                        best_amz = amz_judged[0]
                        amz_url = best_amz.get("page_url")
                        print(f"    [MATCH] Amazonで一致商品を発見 (Score: {best_amz['score']:.1f}%). 詳細解析中...")
                        
                        amz_specs = scrape_amazon_specs(amz_url, browser)
                        if weight_final == "不明" and amz_specs.get("weight") != "不明":
                            weight_final = truncate_weight(amz_specs["weight"])
                            print(f"    [+] Amazonから重量を取得: {weight_final}")
                        if dims_final == "不明" and amz_specs.get("dimensions") != "不明":
                            dims_final = adjust_dimensions(amz_specs["dimensions"])
                            print(f"    [+] Amazonからサイズを取得: {dims_final}")
                    else:
                        print("    [-] Amazonで十分に一致する商品は見つかりませんでした。")
                else:
                    print("    [-] Amazonに該当する商品は見つかりませんでした。")
            except Exception as e:
                print(f"    [!] Amazonスペック補完中にエラー: {e}")

        best_item = tentative_best_item
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
                us_top3 = process_market(token, "EBAY_US", final_en_name, img_url, "NEW")
                uk_top3 = process_market(token, "EBAY_GB", final_en_name, img_url, "NEW")

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
                            print(f"    - URL:      {res['item_url']}")
            else:
                print("[!] eBay APIトークンが取得できなかったため、競合チェックをスキップします。")

        else:
            database.mark_as_researched(item_id, weight=weight_final, dimensions=dims_final)

        print("\n" + "="*60)
        print("                 ✨ リサーチ完了 ✨")
        print("="*60)
        print(f"■ eBay商品 ID  : {item_id}")
        print(f"■ 判定した商品名: {final_name}")
        print("-" * 60)
        
        if best_item:
            print(f"【！国内最安値確定商品！】")
            print(f"■ プラットフォーム: {best_item.get('platform')}")
            print(f"■ 商品タイトル    : {best_item.get('title')}")
            print(f"■ 送料込み価格    : ¥{best_item.get('total_price', 0):,}")
            print(f"■ 商品URL         : {best_item.get('page_url')}")
            print(f"■ 加重平均スコア  : {best_item.get('score', 0):.1f}%")
            print(f"■ 商品の状態      : {best_item.get('condition')}")
        else:
            print(" domestic lowest price NOT found (類似の商品が見つかりませんでした)。")
        
        if high_tariff_flag:
            print(f"\n【⚠️ 重要：高関税注意】")
            print(f"この商品は AI により「{material_label}」素材である可能性が高いと判定されました。")
            print(f"鉄・鋼鉄製品や本革製品は関税が高くなる可能性があるため、仕入れ前に再確認してください。")
            
        print("-" * 60)
        print(f"■ 推定重量        : {weight_final}")
        print(f"■ 推定サイズ      : {dims_final}")
        print("="*60)
        print("[*] データベースへの記録が完了しました。\n")

    except Exception as e:
        print(f"\n[！重大なエラーが発生しました！]")
        print(f"エラー内容: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
