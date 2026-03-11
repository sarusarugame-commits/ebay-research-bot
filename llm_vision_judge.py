import requests
import json
import re
import base64
import time
from config import OPENROUTER_API_KEY, GEMINI_API_KEY

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
QWEN_MODEL   = "qwen/qwen3-5-plus-02-15"

# ─── 共通ヘルパー ────────────────────────────────────────────

def _gemini_post(parts, timeout=20):
    if not GEMINI_API_KEY:
        return None
    api_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    resp = requests.post(api_url, json={"contents": [{"parts": parts}]}, timeout=timeout)
    if resp.status_code == 200:
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    print(f"    [!] Gemini HTTP {resp.status_code}: {resp.text[:120]}")
    return None

def _gemini_with_retry(parts, timeout=20, retries=3):
    for attempt in range(1, retries + 1):
        try:
            result = _gemini_post(parts, timeout=timeout)
            if result is not None:
                if attempt > 1:
                    print(f"    [*] Gemini リトライ {attempt} 回目で成功")
                return result
        except Exception as e:
            print(f"    [!] Gemini 試行 {attempt}/{retries} 失敗: {e}")
        if attempt < retries:
            time.sleep(2)
    return None

def _qwen_post(messages, timeout=25):
    if not OPENROUTER_API_KEY:
        return None
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/eBayResearchSystem",
        "X-Title": "eBay Research System"
    }
    payload = {
        "model": QWEN_MODEL,
        "messages": messages,
        "temperature": 0.0,
        "thinking": {"type": "disabled"}
    }
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=payload, timeout=timeout
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        print(f"    [!] Qwen HTTP {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        print(f"    [!] Qwen 呼び出し失敗: {e}")
    return None

def _download_img_b64(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            return base64.b64encode(r.content).decode("utf-8"), mime
    except Exception:
        pass
    return None, None

def _parse_json(text):
    try:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(m.group()) if m else None
    except Exception:
        return None


# ─── 重量・サイズ推論 ─────────────────────────────────────────

def estimate_weight_with_llm(ebay_img_url, final_name):
    prompt = (
        f"あなたはプロの物流・梱包エキスパートです。添付された商品の画像と商品名（{final_name}）を元に、この商品の重量とサイズを推論してください。\n\n"
        "【推論のステップ】\n"
        "1. 画像と商品名から商品の材質（金属、プラスチック、木材等）と一般的なサイズ感を特定する。\n"
        "2. 外寸サイズ（縦x横x高さ mm）を推測する。\n"
        "3. 推測したサイズと材質から、中身の正味重量（g）を算出する。特に密度（金属は重く、プラスチックは軽い等）を考慮すること。\n"
        "4. 国際発送用の梱包（ダンボール、緩衝材）を含めた最終的なスペックを出す。梱包材の重さとして +100g、サイズとして 縦+20mm, 横+20mm, 高さ+10mm を加算すること。\n\n"
        "【出力形式】\n"
        "必ず以下のJSON形式でのみ出力してください（文章は一切不要です）。\n"
        "{\"weight\": \"数値g\", \"dimensions\": \"縦x横x高さmm\"}"
    )
    print(f"[*] Gemini が画像から重量・サイズを推論しています...")
    img_b64, mime = _download_img_b64(ebay_img_url)
    if img_b64:
        text = _gemini_with_retry([{"text": prompt}, {"inline_data": {"mime_type": mime, "data": img_b64}}])
        if text:
            data = _parse_json(text)
            if data:
                return {"weight": data.get("weight", "不明"), "dimensions": data.get("dimensions", "不明")}

    print(f"    [*] Gemini失敗 → Qwen ({QWEN_MODEL}) でリトライします...")
    text = _qwen_post([{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": ebay_img_url}}
    ]}])
    if text:
        data = _parse_json(text)
        if data:
            return {"weight": data.get("weight", "不明"), "dimensions": data.get("dimensions", "不明")}
    return {"weight": "不明", "dimensions": "不明"}


# ─── 安全性・高関税判定 ───────────────────────────────────────

def analyze_item_safety_and_tariff(ebay_img_url, all_img_urls=None):
    imgs_to_use = (all_img_urls[:5] if all_img_urls else [ebay_img_url])
    multi_prompt = (
        "あなたは税関および物流の専門家です。添付された商品の画像（複数枚）を見て、以下を判定してください。\n\n"
        "1. 【アルコール製品か？】 'true'/'false'\n"
        "2. 【高関税素材か？】 金属または革が含まれるなら 'true'、そうでなければ 'false'\n"
        f"3. 【最良画像インデックス】 {len(imgs_to_use)}枚の画像のうち、商品全体が最もよく写っている画像の番号（0始まり）\n\n"
        "【出力形式】JSONのみ出力（解説不要）\n"
        '{"is_alcohol": bool, "is_high_tariff": bool, "material_label": "素材名またはnull", "best_img_index": 番号}'
    )
    print(f"[*] Gemini が {len(imgs_to_use)} 枚の画像から安全性と最良画像を判定しています...")

    parts = [{"text": multi_prompt}]
    for url in imgs_to_use:
        b64, mime = _download_img_b64(url)
        if b64:
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    text = _gemini_with_retry(parts)

    if not text:
        print(f"    [*] Gemini失敗 → Qwen ({QWEN_MODEL}) でリトライします...")
        content = [{"type": "text", "text": multi_prompt}]
        for url in imgs_to_use:
            content.append({"type": "image_url", "image_url": {"url": url}})
        text = _qwen_post([{"role": "user", "content": content}])

    if text:
        data = _parse_json(text)
        if data:
            best_idx = min(int(data.get("best_img_index", 0)), len(imgs_to_use) - 1)
            result = {
                "is_alcohol":     data.get("is_alcohol", False),
                "is_high_tariff": data.get("is_high_tariff", False),
                "label":          data.get("material_label", "なし"),
                "material_label": data.get("material_label", "なし"),
                "best_img_url":   imgs_to_use[best_idx],
                "best_img_index": best_idx,
            }
            print(f"    [LLM] 安全性・最良画像判定結果: {result}")
            return result
    return {"is_alcohol": False, "is_high_tariff": False, "label": "判定エラー"}


# ─── 画像類似度スコア ─────────────────────────────────────────

def judge_similarity_with_llm(ebay_img_url, scraped_items):
    from concurrent.futures import ThreadPoolExecutor
    items_to_judge = scraped_items[:5]
    print(f"\n[*] LLM (Gemini) を用いた画像比較を開始します（合計 {len(items_to_judge)}件・並列処理）...")
    combined_prompt = (
        "あなたはプロの真贋鑑定士・ECリサーチャーです。以下の指示に従って2つの画像を比較してください。\n\n"
        "【指示】\n"
        "2つの商品画像（画像1: eBayの元画像、画像2: Web検索の候補画像）を比較し、これらが「完全に同一の型番・モデルの商品」である確率を、0から100の数値（スコア）で判定してください。\n"
        "【注意点】\n"
        "- 背景、照明、撮影角度、付属品や箱の有無などは無視してください。\n"
        "- 本体の形状、テクスチャ、ロゴの配置、文字盤のデザインなど「物理的な製品特徴」が一致しているかを厳格に見てください。\n"
        "- 同一製品の別カラーバリエーションは「同一モデル」ではないため、低いスコア（0〜10点）にしてください。\n\n"
        "出力は必ずスコアの数値（例：85）のみとし、文章や理由は一切含めないでください。"
    )
    ebay_b64, ebay_mime = _download_img_b64(ebay_img_url)

    def _judge_one(item):
        mercari_img_url = item.get("img_url")
        if not mercari_img_url:
            item["score"] = 0
            return item

        score = 0
        print(f"  -> 画像比較中: {mercari_img_url[:60]}...")

        cand_b64, cand_mime = _download_img_b64(mercari_img_url)
        if ebay_b64 and cand_b64:
            text = _gemini_with_retry([
                {"text": combined_prompt},
                {"inline_data": {"mime_type": ebay_mime,  "data": ebay_b64}},
                {"inline_data": {"mime_type": cand_mime, "data": cand_b64}},
            ])
            if text:
                m = re.search(r'\d+', text)
                if m:
                    score = int(m.group())

        if score == 0:
            print(f"    [*] Gemini失敗 → Qwen ({QWEN_MODEL}) でリトライします...")
            text = _qwen_post([{"role": "user", "content": [
                {"type": "text", "text": combined_prompt},
                {"type": "image_url", "image_url": {"url": ebay_img_url}},
                {"type": "image_url", "image_url": {"url": mercari_img_url}},
            ]}])
            if text:
                m = re.search(r'\d+', text)
                if m:
                    score = int(m.group())

        item["score"] = score
        return item

    with ThreadPoolExecutor(max_workers=len(items_to_judge)) as ex:
        results = list(ex.map(_judge_one, items_to_judge))

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ─── 型番一致・コンディション判定 ────────────────────────────

def verify_model_match(ref_img_url, candidate_img_url, model_number, condition_text, ref_title=""):
    prompt = f"""
あなたはプロのeBayセラー兼鑑定士です。以下の2枚の画像を比較し、正確な鑑定を行ってください。

【鑑定する型番/モデル】: {model_number}
【参照商品タイトル（画像1）】: {ref_title if ref_title else "不明"}

【国内サイトの商品説明テキスト】:
{condition_text}

【タスク1: 同一性判定】
画像1（eBay参照画像）と画像2（候補画像）を比較し、これらが「完全に同じ型番・モデル・カラー」であるか判定してください。
- 背景、照明、撮影角度、付属品や箱の有無などは無視してください。
- 本体の形状、テクスチャ、ロゴの配置、文字盤のデザインなど「物理的な製品特徴」が一致しているかを厳格に見てください。
- 同一製品の別カラーバリエーションや世代違いは "match": false としてください。
- 【特に重要】型番に「G2」「G3」「Mark II」「第2世代」などの世代識別子が含まれる場合、元の型番と世代違いであれば必ず "match": false にしてください。例：「URSA Mini Pro 4.6K」と「URSA Mini Pro 4.6K G2」は別製品です。
- 【仕様差異の判定】参照商品タイトルにレンズマウント・カラー・容量・周波数帯など特定の仕様が明記されている場合、候補商品がその仕様と異なれば "match": false としてください。タイトルに仕様の記載がない場合は仕様差異を無視してください。

【タスク2: コンディション判定】
画像2の状態と商品説明テキストから、eBayの出品に最適なコンディションを以下から1つ選んでください。
- Brand new (新品・未開封)
- Like New (未使用に近い)
- Very Good (目立った傷なし、非常に良い中古)
- Good (一般的な中古、やや傷あり)
- Acceptable (かなりの使用感、目立つ傷あり、動作に問題なし)
- For parts or not working (故障品、パーツ取り)

回答は必ず以下のJSON形式のみで返してください（解説は不要です）。
{{
    "match": true/false,
    "condition": "選択したコンディション名",
    "reason": "不一致の場合の短い理由"
}}
"""
    print(f"    [LLM] 型番一致・状態判定中: {model_number}...")

    ref_b64,  ref_mime  = _download_img_b64(ref_img_url)
    cand_b64, cand_mime = _download_img_b64(candidate_img_url)
    if ref_b64 and cand_b64:
        text = _gemini_with_retry([
            {"text": prompt},
            {"inline_data": {"mime_type": ref_mime,  "data": ref_b64}},
            {"inline_data": {"mime_type": cand_mime, "data": cand_b64}},
        ])
        if text:
            data = _parse_json(text)
            if data:
                is_match  = bool(data.get("match", False))
                condition = data.get("condition", "Good")
                label = "✅ 一致" if is_match else "❌ 不一致"
                print(f"    [LLM/Gemini] {label} | 判定状態: {condition} | 理由: {data.get('reason','不明')}")
                return is_match, condition

    print(f"    [*] Gemini失敗 → Qwen ({QWEN_MODEL}) でリトライします...")
    text = _qwen_post([{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": ref_img_url}},
        {"type": "image_url", "image_url": {"url": candidate_img_url}},
    ]}])
    if text:
        data = _parse_json(text)
        if data:
            is_match  = bool(data.get("match", False))
            condition = data.get("condition", "Good")
            label = "✅ 一致" if is_match else "❌ 不一致"
            print(f"    [LLM/Qwen] {label} | 判定状態: {condition} | 理由: {data.get('reason','不明')}")
            return is_match, condition

    return True, "Good"
