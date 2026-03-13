import requests
import json
import time
import re
import base64
from collections import Counter
from config import OPENROUTER_API_KEY, GEMINI_API_KEY
import database
from PIL import Image
import io

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
QWEN_MODEL   = "qwen/qwen3.5-flash-02-23"


JP_PATTERN = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF]')

def get_word_frequencies(titles):
    """タイトル群から日本語単語の出現頻度をカウントした辞書を返す"""
    all_words_raw = []
    for title in titles:
        clean_title = re.sub(r'[【】\[\]（）()!！?？♪☆★*＊/／¥,]+', ' ', title)
        words = [w for w in clean_title.split() if len(w) > 1 and JP_PATTERN.search(w)]
        all_words_raw.extend(words)
    counts = Counter(all_words_raw)
    freq_list = {w: counts[w] for w in counts if counts[w] >= 2}
    return freq_list

def log_frequent_words(freq_data, label):
    if freq_data:
        sorted_freq = sorted(freq_data.items(), key=lambda x: x[1], reverse=True)
        freq_str = ", ".join([f"{w}({c})" for w, c in sorted_freq[:10]])
        print(f"    [*] {label}頻出単語 (Top 10): {freq_str}")

def _download_img_b64(url, max_size=1024):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            img = Image.open(io.BytesIO(r.content))
            
            # リサイズ処理 (長辺を max_size に制限)
            w, h = img.size
            if max(w, h) > max_size:
                scale = max_size / max(w, h)
                new_w, new_h = int(w * scale), int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                
                # リサイズされた画像をバイト列に変換
                buf = io.BytesIO()
                # MIMEタイプに合わせて保存形式を選択
                fmt = "JPEG" if "jpeg" in mime.lower() else "PNG"
                img.save(buf, format=fmt, quality=85 if fmt == "JPEG" else None)
                return base64.b64encode(buf.getvalue()).decode("utf-8"), mime
            
            return base64.b64encode(r.content).decode("utf-8"), mime
    except Exception:
        pass
    return None, None

def _gemini_extract(prompt, img_url=None):
    if not GEMINI_API_KEY:
        return None
    api_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    parts = []
    if img_url:
        b64, mime = _download_img_b64(img_url)
        if b64:
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    parts.append({"text": prompt})
    for attempt in range(1, 4):
        try:
            resp = requests.post(api_url, json={"contents": [{"parts": parts}]}, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                # トークン情報の取得と記録
                usage = data.get("usageMetadata", {})
                database.log_token_usage(
                    GEMINI_MODEL, 
                    usage.get("promptTokenCount", 0), 
                    usage.get("candidatesTokenCount", 0),
                    usage.get("thinkingTokenCount", 0)
                )
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            pass
        if attempt < 3:
            time.sleep(2)
    return None

def _qwen_extract(prompt, img_url=None):
    if not OPENROUTER_API_KEY:
        return None
    content = []
    if img_url:
        content.append({"type": "image_url", "image_url": {"url": img_url}})
    content.append({"type": "text", "text": prompt})
    payload = {
        "model": QWEN_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "thinking": {"type": "disabled"}
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/eBayResearchSystem",
        "X-Title": "eBay Research System"
    }
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=25)
        if resp.status_code == 200:
            data = resp.json()
            # トークン情報の取得と記録
            usage = data.get("usage", {})
            database.log_token_usage(
                QWEN_MODEL, 
                usage.get("prompt_tokens", 0), 
                usage.get("completion_tokens", 0),
                usage.get("reasoning_tokens", 0)
            )
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return None

def parse_llm_json(content):
    try:
        json_str = re.sub(r"```json\s?|```", "", content).strip()
        match = re.search(r"(\{.*\})", json_str, re.DOTALL)
        if match:
            json_str = match.group(1)
        return json.loads(json_str)
    except Exception:
        return None

def extract_product_name(ebay_title, scored_candidates=None, img_url=None):
    scored_candidates = scored_candidates or []
    titles = [c['title'] for c in scored_candidates if c.get('title')]
    freq_data = get_word_frequencies(titles)
    log_frequent_words(freq_data, "国内タイトル")
    freq_hint = json.dumps(freq_data, ensure_ascii=False) if freq_data else "（なし）"
    img_note = "添付の商品画像も参考にしてください。" if img_url else ""
    prompt = (
        "あなたはプロのEC商品アナリストです。日本のマーケットで最も正確に検索できる商品名をJSONで出力してください。\n\n"
        f"【eBayタイトル】: {ebay_title}\n"
        f"【国内頻出単語】: {freq_hint}\n\n"
        f"{img_note}\n"
        "ルール:\n"
        "1. brand・seriesは必ず日本語表記（カタカナ/漢字）で出力する。例: SEIKOは「セイコー」、ALBAは「アルバ」。\n"
        "2. modelは型番をそのままアルファベット/数字で出力する。例: AQPK401\n"
        "3. full_nameは国内ECサイトで検索する際のクエリ文字列。brand + series + model を日本語表記で構成する。\n"
        "4. JSON形式: {\"brand\": \"\", \"series\": \"\", \"model\": \"\", \"keywords\": \"\", \"full_name\": \"\"}\n"
        "5. brandは正式な日本語名称を使用。"
    )
    print(f"[*] AI ({QWEN_MODEL}) で商品名を抽出中...")
    text = _qwen_extract(prompt, img_url=img_url)
    if not text:
        text = _gemini_extract(prompt, img_url=img_url)
    if text:
        data = parse_llm_json(text)
        brand = data.get("brand", "")
        series = data.get("series", "")
        model = data.get("model", "")
        keywords = data.get("keywords", "")
        # keywordsは内部保持のみ。full_nameはbrand+series+modelだけで構築（重複防止）
        # 単語レベルで重複除去（順序保持）
        seen, deduped = set(), []
        for word in f"{brand} {series} {model}".split():
            key = word.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(word)
        # LLMがfull_nameを返した場合はそれを優先、なければコード側で結合
        llm_full_name = data.get("full_name", "").strip()
        full_name = llm_full_name if llm_full_name else " ".join(deduped).strip() or ebay_title
        print(f"    - 抽出成功: {full_name}")
        return {
            "brand": brand, "series": series, "model": model, "keywords": keywords,
            "full_name": full_name
        }
    return {"brand": "", "series": "", "model": "", "keywords": "", "full_name": ebay_title}

def extract_ebay_search_query(ebay_title, scored_candidates=None):
    """eBay検索クエリ（型番）をLLMに生成させる。
    - scored_candidates がある場合: 海外候補タイトルの頻出語をヒントに抽出
    - ない場合: 元タイトルのみからLLMに推論させる
    戻り値: {"model": "", "full_name": ""}
    """
    if scored_candidates:
        titles = [c['title'] for c in scored_candidates if c.get('title')]
        freq_data = get_word_frequencies(titles)
        log_frequent_words(freq_data, "海外タイトル")
        freq_hint = json.dumps(freq_data, ensure_ascii=False) if freq_data else "（なし）"
        hint_section = f"【海外候補の頻出語】: {freq_hint}\n上記の頻出語も参考に、"
    else:
        hint_section = "海外候補は取得できませんでした。元タイトルだけから推論して、"

    prompt = (
        "あなたはeBay商品検索の専門家です。\n"
        f"【元タイトル】: {ebay_title}\n"
        f"{hint_section}"
        "eBayで同一商品を検索するための最適なクエリをJSONで出力してください。\n\n"
        "ルール:\n"
        "1. modelには正確な型番を入れる（例: MRG-BF1000R-1AJR）。末尾의JR・JF等の国内サフィックスも必ず含める。\n"
        "2. full_nameはeBay検索クエリとして使う文字列。brand + series + model の順で構成。\n"
        "3. 型番が不明な場合はブランド名＋シリーズ名で構成する。\n"
        "4. 出力はJSONのみ: {\"brand\": \"\", \"series\": \"\", \"model\": \"\", \"full_name\": \"\"}"
    )
    print(f"[*] AI ({QWEN_MODEL}) でeBay検索クエリを生成中...")
    text = _qwen_extract(prompt)
    if not text:
        text = _gemini_extract(prompt)
    if text:
        data = parse_llm_json(text)
        if data:
            brand = data.get("brand", "")
            series = data.get("series", "")
            model = data.get("model", "")
            full_name = data.get("full_name", "")
            if not full_name:
                seen, deduped = set(), []
                for word in f"{brand} {series} {model}".split():
                    key = word.lower()
                    if key not in seen:
                        seen.add(key)
                        deduped.append(word)
                full_name = " ".join(deduped).strip() or ebay_title
            print(f"    - 抽出成功: {full_name} (型番: {model})")
            return {"brand": brand, "series": series, "model": model, "full_name": full_name}
    print(f"    - 抽出失敗。元タイトルを使用: {ebay_title}")
    return {"brand": "", "series": "", "model": "", "full_name": ebay_title}

def extract_english_product_name(ebay_title, scored_candidates):
    titles = [c['title'] for c in scored_candidates if c.get('title')]
    freq_data = get_word_frequencies(titles)
    log_frequent_words(freq_data, "海外タイトル")
    freq_hint = json.dumps(freq_data, ensure_ascii=False) if freq_data else "（なし）"
    prompt = (
        "分析し、eBayで最も検索されやすい英語の商品名をJSONで出力してください。\n\n"
        f"【元タイトル】: {ebay_title}\n"
        f"【頻出語】: {freq_hint}\n\n"
        "出力形式: {\"brand\": \"\", \"series\": \"\", \"model\": \"\", \"keywords\": \"\"}"
    )
    print(f"[*] AI ({QWEN_MODEL}) で英語商品名を抽出中...")
    text = _qwen_extract(prompt, img_url=None)
    if not text:
        text = _gemini_extract(prompt, img_url=None)
    if text:
        data = parse_llm_json(text)
        if data:
            brand, series, model, keywords = data.get("brand", ""), data.get("series", ""), data.get("model", ""), data.get("keywords", "")
            # keywordsは内部保持のみ。full_nameはbrand+series+modelだけで構築（重複防止）
            # 単語レベルで重複除去（順序保持）
            seen, deduped = set(), []
            for word in f"{brand} {series} {model}".split():
                key = word.lower()
                if key not in seen:
                    seen.add(key)
                    deduped.append(word)
            full_name = " ".join(deduped).strip() or ebay_title
            print(f"    - 抽出成功: {full_name}")
            return {"brand": brand, "series": series, "model": model, "keywords": keywords, "full_name": full_name}
    return {"brand": "", "series": "", "model": "", "keywords": "", "full_name": ebay_title}
