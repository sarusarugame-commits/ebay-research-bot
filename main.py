import gpu_utils
import sys
import io
import re
import datetime
import os as _os

# WindowsでANSIエスケープを有効化
if sys.platform == "win32":
    _os.system("")
BLUE = "\033[94m"
RESET = "\033[0m"


def hyperlink(url, text=None):
    """OSC 8ハイパーリンク。Windows Terminal / VSCode対応。非対応端末はURLをそのまま表示。"""
    raw = text if text else url
    label = raw[:30] + "…" if len(raw) > 30 else raw
    return f"\033]8;;{url}\033\\{BLUE}{label}{RESET}\033]8;;\033\\"


import traceback
import functools
import os
import requests


def global_exception_handler(exctype, value, tb):
    print("\n" + "=" * 50)
    print("!!! UNCAUGHT EXCEPTION !!!")
    traceback.print_exception(exctype, value, tb)
    print("=" * 50 + "\n")


sys.excepthook = global_exception_handler

# Windows環境でのエンコードエラー（CP932）対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
print = functools.partial(print, flush=True)

from config import (
    EBAY_APP_ID,
    GOOGLE_APPLICATION_CREDENTIALS,
    RAKUTEN_APPLICATION_ID,
    RAKUTEN_ACCESS_KEY,
    RAKUTEN_AFFILIATE_ID,
    YAHOO_CLIENT_ID,
)
import database
from ebay_api import get_item_details
from mercari_scraper import search_mercari, search_rakuma, scrape_item_data
from surugaya_scraper import search_surugaya, scrape_surugaya_item
from llm_vision_judge import estimate_weight_with_llm, analyze_item_safety_and_tariff
from clip_judge_client import judge_similarity

# verify_with_lightglue は clip_judge 内で使われるようになったよ！
from ebay_scraper import (
    scrape_ebay_newest_items,
    scrape_ebay_item_specs,
    get_browser_page,
    ChromiumOptions,
    ChromiumPage,
)
from vision_search import find_similar_images_on_web
from llm_namer import extract_product_name
from shopping_api import search_rakuten, search_yahoo, scrape_yahoo_item
from amazon_scraper import search_amazon, search_amazon_via_google, scrape_amazon_specs
from llm_vision_judge import verify_model_match
from sheets_writer import write_to_sheet

GREEN = "\033[92m"
RESET_GREEN = "\033[0m"


def get_fresh_browser():
    """並列処理用に新しいポートでブラウザを起動する"""
    co = ChromiumOptions()
    co.auto_port()
    co.headless(True)
    co.set_argument("--window-size=1280,720")
    return ChromiumPage(co)


def extract_specs_from_text(text):
    w, d = "不明", "不明"
    if not text:
        return w, d
    w_m = re.search(r"(\d+(\.\d+)?)\s?(kg|g|キロ|グラム)", text, re.I)
    if w_m:
        w = w_m.group(0)
    d_m = re.search(
        r"(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?(cm|mm|センチ|インチ|in)?",
        text,
        re.I,
    )
    if d_m:
        d = d_m.group(0)
    return w, d


def truncate_weight(w_str):
    if not w_str or w_str == "不明":
        return "不明"
    m = re.search(r"(\d+(\.\d+)?)", str(w_str))
    if m:
        val = float(m.group(1))
        unit = "kg" if "kg" in str(w_str).lower() or "キロ" in str(w_str) else "g"
        if unit == "g" and val > 2000:
            return f"{val/1000:.1f}kg"
        return f"{val}{unit}"
    return w_str


def adjust_dimensions(d_str):
    if not d_str or d_str == "不明":
        return "不明"
    return d_str.replace(" ", "")


def main():
    database.init_db()

    # 1. ターゲット商品リストを取得（eBayから新着スクレイピング）
    # ※ 本来はAPIを使いたいが、まずはスクレイピング版
    # 実際にはURL指定でも動くようにする想定
    print(f"\n[*] eBayから新着商品 (Category: 261898) を取得中...")
    ebay_items = scrape_ebay_newest_items(category_id=261898, count=5)

    if not ebay_items:
        print("[!] eBay商品が取得できませんでした。時間をおいて再試行してください。")
        return

    for target_item in ebay_items:
        item_id = target_item["item_id"]
        img_url = target_item["img_url"]

        print("\n" + "=" * 80)
        print(f"[*] ターゲット商品: {target_item['title']}")
        print(f"[*] eBay ID: {item_id} | Price: ${target_item['price_usd']}")
        print(f"[*] eBay URL: {target_item['page_url']}")

        # 2. LLMによる日本語商品名・型番の特定
        print("\n[*] LLMで日本語商品名を特定中...")
        name_data = extract_product_name(target_item["title"])
        final_name = name_data.get("full_name", "特定不能")
        print(f" -> 最終確定した日本語名: {final_name}")

        # ==========================================
        # 英語商品名・eBay検索クエリの特定
        print("\n[*] LLMでeBay検索クエリ（型番）を生成中...")
        from llm_namer import extract_ebay_search_query

        en_query_data = extract_ebay_search_query(target_item.get("title"))
        final_en_name = en_query_data.get("full_name", target_item.get("title"))
        print(f" -> 最終確定した英語名: {final_en_name}")

        # デバッグ画像用フォルダの作成
        debug_folder = (
            f"debug_images/{item_id}_{datetime.datetime.now().strftime('%H%M%S')}"
        )
        # save_debug_image(img_url, debug_folder, "00_ebay_reference.jpg") # ユーティリティがないため省略

        # 4. 国内横断検索 (2段階フェーズ)
        all_domestic_candidates = []
        weight_final = "不明"
        dims_final = "不明"
        origin_final = "不明"
        tentative_best_item = None
        tentative_best_price = float("inf")
        domestic_thresholds = {}

        if final_name and final_name != "特定不能":
            # 検索クエリ = LLMが確定した日本語名をそのまま使う
            search_query = final_name

            # フィルタリング用キーワード
            import unicodedata

            def normalize_text(text):
                return unicodedata.normalize("NFKC", text).lower()

            model_name = normalize_text(name_data.get("model", ""))
            series_name = normalize_text(name_data.get("series", ""))

            def collect_candidates(candidates, platform, browser=None):
                if not candidates:
                    print(f"[*] {platform}: 検索結果 0 件でした。")
                    return []

                print(f"[*] {platform}: {len(candidates)} 件の候補を収集中...")

                _EXCLUDE_WORDS = ["レンタル", "rental", "パーツ", "修理", "ジャンク", "部品取り", "訳あり", "難あり"]
                base_filtered = []
                for item in candidates:
                    title_norm = normalize_text(item.get("title", ""))
                    if any(w in title_norm for w in _EXCLUDE_WORDS):
                        continue
                    base_filtered.append(item)

                keyword_filtered = []
                for item in base_filtered:
                    title_norm = normalize_text(item.get("title", ""))
                    has_model = (model_name in title_norm) if len(model_name) >= 3 else False
                    series_head = normalize_text(series_name.split()[0]) if series_name.split() else ""
                    has_series = (series_name in title_norm or (len(series_head) >= 2 and series_head in title_norm)) if len(series_name) >= 2 else False

                    if has_model or has_series:
                        item["platform"] = platform
                        keyword_filtered.append(item)

                if not keyword_filtered:
                    print(f"    [!] キーワード一致なし、除外ワード適用済みの全件を対象にします。")
                    for item in base_filtered: item["platform"] = platform
                    keyword_filtered = base_filtered

                # 詳細情報の並列取得
                collected = []
                # ここも並列化 (各サイト2〜3件程度)
                to_scrape = keyword_filtered[:3]
                
                # 詳細取得用のブラウザを確保 (なければ作成、ある場合は流用)
                local_browser = browser if browser else get_fresh_browser()
                
                try:
                    for item in to_scrape:
                        page_url = item["page_url"]
                        platform = item["platform"]
                        detail = None
                        try:
                            if platform in ["メルカリ", "ラクマ"]:
                                detail = scrape_item_data(page_url, local_browser)
                            elif platform == "駿河屋":
                                detail = scrape_surugaya_item(page_url, local_browser)
                            elif platform == "Yahooショッピング":
                                detail = scrape_yahoo_item(page_url, local_browser)
                        except Exception as e:
                            print(f"    [!] {platform} 詳細スクレイピング失敗: {e}")
                        
                        if detail: item.update(detail)
                        
                        # 詳細取得後の追加除外チェック (コンディション・説明文にジャンク等が含まれる場合は除外)
                        condition_text = normalize_text(item.get("condition", ""))
                        desc_text = normalize_text(item.get("description", ""))
                        if any(w in condition_text for w in ["全体的に状態が悪い", "ジャンク", "難あり", "訳あり"]):
                            continue
                        if "ジャンク" in desc_text:
                            continue
                        
                        current_img_urls = item.get("img_urls", [])
                        if not current_img_urls and item.get("img_url"):
                            current_img_urls = [item["img_url"]]
                        
                        if current_img_urls:
                            item["_all_img_urls"] = current_img_urls
                            collected.append(item)
                finally:
                    if not browser: # 内部で作ったブラウザなら閉じる
                        local_browser.quit()
                
                return collected

            print(f"\n[*] 国内5大プラットフォームを並列調査中（収集フェーズ）...")

            from concurrent.futures import ThreadPoolExecutor, as_completed

            _series = name_data.get("series", "")
            _model = name_data.get("model", "")
            _jp_name = final_name.replace(_series, "").replace(_model, "").strip(" ,、")
            api_query = f"{_series} {_model} {_jp_name}".strip() or search_query

            # ブラウザ系（BAN対策で3並列上限）
            browser_tasks = [
                (
                    "メルカリ",
                    lambda: search_mercari(
                        search_query, get_fresh_browser(), max_results=15
                    ),
                ),
                (
                    "ラクマ",
                    lambda: search_rakuma(
                        search_query, get_fresh_browser(), max_results=10
                    ),
                ),
                (
                    "駿河屋",
                    lambda: search_surugaya(
                        search_query, get_fresh_browser(), max_results=10
                    ),
                ),
            ]
            # API系（制限緩め、2並列）
            api_tasks = [
                ("楽天市場", lambda: search_rakuten(api_query)),
                ("Yahooショッピング", lambda: search_yahoo(api_query)),
            ]

            def _run_task(name_fn):
                name, fn = name_fn
                try:
                    return name, fn()
                except Exception as e:
                    print(f"    [!] {name} 収集エラー: {e}")
                    return name, []

            # ブラウザ3並列 + API2並列 を同時実行
            all_futures = {}
            with ThreadPoolExecutor(max_workers=5) as ex:
                for t in browser_tasks + api_tasks:
                    all_futures[ex.submit(_run_task, t)] = t
                for future in as_completed(all_futures):
                    t = all_futures[future]
                    name, res = future.result()
                    # 検索に使用したブラウザを特定（あれば）
                    used_browser = None
                    all_domestic_candidates.extend(collect_candidates(res, name))

            # ── 国内一括判定フェーズ ──
            if all_domestic_candidates:
                print(
                    f"\n[*] 国内全候補 ({len(all_domestic_candidates)}件) を一括画像判定中..."
                )
                # サーバーに送る形式に変換 (1商品複数画像対応のため、フラットに展開)
                server_payload = []
                for idx, cand in enumerate(all_domestic_candidates):
                    for img_url_target in cand["_all_img_urls"]:
                        server_payload.append(
                            {
                                "img_url": img_url_target,
                                "page_url": cand.get("page_url"),
                                "_cand_idx": idx,
                            }
                        )

                judged_list, thresholds = judge_similarity(img_url, server_payload)
                domestic_thresholds = thresholds

                # スコアを商品ごとに集約（複数画像のうち最高スコアを採用）
                final_results = []
                for idx, cand in enumerate(all_domestic_candidates):
                    cand_scores = [
                        float(item.get("score", 0))
                        for item in judged_list
                        if item.get("_cand_idx") == idx
                    ]
                    best_score = max(cand_scores) if cand_scores else 0

                    if best_score > 0:
                        cand["score"] = best_score
                        # 最高スコアの画像URLを特定
                        best_item_ji = next(
                            item
                            for item in judged_list
                            if item.get("_cand_idx") == idx
                            and float(item.get("score", 0)) == best_score
                        )
                        cand["best_img_url"] = best_item_ji.get("img_url")

                        # 価格・送料計算
                        p_str = re.sub(r"[^\d]", "", str(cand.get("price", "0")))
                        price_val = int(p_str) if p_str else 0
                        cand["price_int"] = price_val
                        ship_fee = cand.get("actual_shipping_fee", 0)
                        total_price = price_val + ship_fee
                        cand["total_price"] = total_price

                        # 暫定最安値更新
                        if total_price < tentative_best_price:
                            tentative_best_price = total_price
                            tentative_best_item = cand

                        # スペック補完
                        if weight_final == "不明" or dims_final == "不明":
                            desc_text = (
                                cand.get("description", "")
                                + " "
                                + cand.get("title", "")
                            )
                            ext_w, ext_d = extract_specs_from_text(desc_text)
                            if weight_final == "不明" and ext_w != "不明":
                                weight_final = ext_w
                            if dims_final == "不明" and ext_d != "不明":
                                dims_final = ext_d

                        final_results.append(cand)

                final_candidates = final_results
                print(
                    f"[*] 国内判定完了: {len(final_candidates)} 件合格 (D閾値:{thresholds.get('dino'):.1f} C閾値:{thresholds.get('color'):.1f})"
                )

        if weight_final == "不明" and 'raw_w' in locals() and raw_w != "不明":
            weight_final = truncate_weight(raw_w) # type: ignore
        if dims_final == "不明" and 'raw_d' in locals() and raw_d != "不明":
            dims_final = adjust_dimensions(raw_d) # type: ignore

        # 5. 【最終補完】スペック情報（重量/サイズ）の補完
        amz_urls_checked = []  # 参照したAmazon URLを収集
        amz_search_url = None  # Amazon検索URL（取得できなくても表示用に保持）
        if weight_final == "不明" or dims_final == "不明":
            # --- STEP 5.1: Amazon からの補完 ---
            print(
                "\n[*] 重量またはサイズが不明なため、Amazonからスペック情報を補完中..."
            )
            try:
                browser = get_fresh_browser()
                amz_search_query = (
                    final_name if final_name != "特定不能" else target_item.get("title")
                )
                import re as _re

                amz_search_url = f"https://www.amazon.co.jp/s?k={_re.sub(r'[\\s　]+', '+', amz_search_query)}"
                amz_results = search_amazon(amz_search_query, browser, max_results=5)

                amz_for_judge = [
                    {
                        "img_url": r.get("img_url", ""),
                        "page_url": r.get("page_url", ""),
                        "_orig": r,
                    }
                    for r in amz_results
                    if r.get("img_url")
                ]

                if amz_for_judge:
                    amz_judged_list, _ = judge_similarity(img_url, amz_for_judge)

                    if amz_judged_list and amz_judged_list[0].get("score", 0) >= 70:
                        best_amz = amz_judged_list[0]
                        amz_url = best_amz.get("page_url") or best_amz.get(
                            "_orig", {}
                        ).get("page_url")
                        if amz_url:
                            amz_urls_checked.append(amz_url)
                            print(
                                f"    [MATCH] Amazonで一致商品を発見 (Score: {best_amz['score']:.1f}%)."
                            )
                            amz_specs = scrape_amazon_specs(amz_url, browser)
                            if (
                                weight_final == "不明"
                                and amz_specs.get("weight") != "不明"
                            ):
                                weight_final = truncate_weight(amz_specs["weight"])
                            if (
                                dims_final == "不明"
                                and amz_specs.get("dimensions") != "不明"
                            ):
                                dims_final = adjust_dimensions(amz_specs["dimensions"])

                browser.quit()

            except Exception as e:
                print(f"    [!] Amazonスペック補完エラー: {e}")

        # 6. リサーチ結果の記録 (DB & Spreadsheet)
        print("\n[*] リサーチ結果を記録中...")
        profit_info = {}  # 利益計算等

        # 為替レート (150円固定)
        EXCHANGE_RATE = 150.0

        res_data = {
            "ebay_id": item_id,
            "ebay_title": target_item["title"],
            "ebay_price_usd": target_item["price_usd"],
            "ebay_url": target_item["page_url"],
            "domestic_best_price": tentative_best_price,
            "domestic_best_url": tentative_best_item.get("page_url") if tentative_best_item else "",
            "domestic_platform": tentative_best_item.get("platform") if tentative_best_item else "",
            "weight": weight_final,
            "dimensions": dims_final,
            "profit": 0,  # 本来は計算
            "condition": tentative_best_item.get("condition", "不明") if tentative_best_item else "",
        }

        # 利益 / 送料の概算 (1kg=3000円換算など)
        # ※ ここでは簡易表示のみ
        print(f" -> 最安仕入先: {res_data['domestic_platform']} | 価格: ¥{res_data['domestic_best_price']:,}")
        print(f" -> 推定重量: {res_data['weight']} | eBay価格: ${res_data['ebay_price_usd']}")

        # スプレッドシート書込み
        try:
            write_to_sheet(res_data)
            print(" -> Googleスプレッドシートへの記録が完了しました。")
        except Exception as e:
            print(f" [!] スプレッドシート書込み失敗: {e}")

        # DB保存
        database.save_research_result(res_data)
        print(" -> ローカルDBへの保存が完了しました。")

    print("\n" + "=" * 80)
    print("[*] 全商品のリサーチが完了しました。")


if __name__ == "__main__":
    main()
