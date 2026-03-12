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
QWEN_MODEL   = "qwen/qwen-3.5-flash"


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
                img.save(buf, format="JPEG")
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return f"data:image/jpeg;base64,{b64}"
            
            b64 = base64.b64encode(r.content).decode("utf-8")
            return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"    [IMG_ERROR] {e}")
    return None

def extract_product_name(ebay_title, domestic_candidates, img_url=None):
    """
    eBayタイトルと国内候補のタイトル群、画像を元に、
    1. 日本語の正式商品名
    2. 型番（あれば）
    3. シリーズ名（あれば）
    を抽出する。
    """
    print(f"[*] AIによる商品名解析を開始 (Qwen 3.5 Flash)...")
    
    titles_list = [c.get("title", "") for c in domestic_candidates]
    freq_data = get_word_frequencies(titles_list)
    log_frequent_words(freq_data, "国内商品")

    # Qwen Vision へのプロンプト
    prompt = f"""
eBay Item Title: {ebay_title}
Domestic Candidate Titles:
{chr(10).join(['- ' + t for t in titles_list[:10]])}

Frequency Data: {json.dumps(freq_data, ensure_ascii=False)}

Task:
Based on the eBay title, domestic titles, and the image provided, identify the official Japanese product name.
Extract the "series name" and "model number" if applicable.
The "full_name" should be the most characteristic and official Japanese name of the product.

Output format (JSON):
{{
  "full_name": "日本語正式商品名",
  "series": "シリーズ名",
  "model": "型番",
  "title_en": "English Product Name"
}}

Rules:
- Respond ONLY in the specified JSON format.
- Output Japanese officially and naturally.
"""

    payload = {{
        "model": QWEN_MODEL,
        "messages": [
            {{
                "role": "user",
                "content": [
                    {{"type": "text", "text": prompt}}
                ]
            }}
        ],
        "response_format": {{ "type": "json_object" }}
    }}

    if img_url:
        img_url_target = img_url
        img_b64 = _download_img_b64(img_url_target)
        if img_b64:
            payload["messages"][0]["content"].append({{
                "type": "image_url",
                "image_url": {{ "url": img_b64 }}
            }})

    try:
        headers = {{
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }}
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            res_json = json.loads(content)
            
            usage = data.get("usage", {{}})
            database.log_token_usage(QWEN_MODEL, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            
            return res_json
    except Exception as e:
        print(f"    [LLM_ERROR] {e}")
    
    return {{
        "full_name": ebay_title,
        "series": "",
        "model": "",
        "title_en": ebay_title
    }}

def extract_ebay_search_query(ebay_title):
    """
    eBayのタイトルから、再検索に最適な英語の「型番＋主要ワード」を抽出する。
    """
    print(f"[*] eBay検索クエリの最適化中 (Qwen 3.5 Flash)...")
    
    prompt = f"""
Analyze the following eBay listing title and extract the core product name and model number for a clean eBay search query.
Exclude "junk" words like "New", "Free Shipping", "Japan", etc.

eBay Title: {ebay_title}

Output format (JSON):
{{
  "full_name": "Optimized Core Query",
  "model": "Model Number Only"
}}
"""
    payload = {{
        "model": QWEN_MODEL,
        "messages": [
            {{
                "role": "user",
                "content": prompt
            }}
        ],
        "response_format": {{ "type": "json_object" }}
    }}

    try:
        headers = {{
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }}
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {{}})
            database.log_token_usage(QWEN_MODEL, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            return json.loads(content)
    except Exception as e:
        print(f"    [LLM_ERROR] {e}")

    return {{
        "full_name": ebay_title,
        "model": ""
    }}
