import requests
import json
import re
import base64
import time
from config import OPENROUTER_API_KEY, GEMINI_API_KEY
import database
from PIL import Image
import io

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
QWEN_MODEL   = "qwen/qwen-3.5-flash"

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
        data = resp.json()
        usage = data.get("usageMetadata", {})
        database.log_token_usage(
            GEMINI_MODEL, 
            usage.get("promptTokenCount", 0), 
            usage.get("candidatesTokenCount", 0),
            usage.get("thoughtTokenCount", 0)
        )
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except:
            return None
    return None

def _download_img_b64(url, max_size=1024):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content))
            w, h = img.size
            if max(w, h) > max_size:
                scale = max_size / max(w, h)
                img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"    [IMG_ERROR] {e}")
    return None

def _qwen_post_vision(prompt, img_url, timeout=30):
    img_b64 = _download_img_b64(img_url)
    if not img_b64: return None
    
    payload = {{
        "model": QWEN_MODEL,
        "messages": [
            {{
                "role": "user",
                "content": [
                    {{"type": "text", "text": prompt}},
                    {{"type": "image_url", "image_url": {{ "url": img_b64 }}}}
                ]
            }}
        ],
        "response_format": {{ "type": "json_object" }}
    }}
    
    headers = {{
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }}
    
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            usage = data.get("usage", {{}})
            database.log_token_usage(QWEN_MODEL, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"    [QWEN_ERROR] {e}")
    return None

# ─── 個別機能 ───────────────────────────────────────────────

def analyze_item_safety_and_tariff(main_img_url, all_img_urls=[]):
    """
    主力画像とサブ画像から
    - アルコール成分の有無
    - 高関税素材（革、鉄、鋼鉄など）
    - 特定された素材名
    - 商品特定に最も適した画像URL
    を判定する。
    """
    print(f"[*] 商品の素材・安全性を解析中 (Qwen 3.5 Flash)...")
    
    # 解析対象画像を最大4枚に制限
    target_imgs = [main_img_url] + [u for u in all_img_urls if u != main_img_url]
    target_imgs = target_imgs[:4]
    
    prompt = """
Analyze the provided images of this product.
1. Check if it is an alcoholic beverage (is_alcohol).
2. Check if it contains high-tariff materials such as 'leather', 'iron', or 'steel' (is_high_tariff).
3. Identify the primary material (label).
4. Select the best image index (0-based) for product identification (best_img_idx).

Output JSON:
{
  "is_alcohol": bool,
  "is_high_tariff": bool,
  "label": "Japanese material name",
  "best_img_idx": int
}
"""
    # 複数画像を含むQwenリクエスト
    content_list = [{"type": "text", "text": prompt}]
    for url in target_imgs:
        b64 = _download_img_b64(url)
        if b64:
            content_list.append({"type": "image_url", "image_url": {"url": b64}})
            
    payload = {{
        "model": QWEN_MODEL,
        "messages": [{{ "role": "user", "content": content_list }}],
        "response_format": {{ "type": "json_object" }}
    }}
    
    headers = {{ "Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json" }}
    
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
        if resp.status_code == 200:
            data = resp.json()
            res = json.loads(data["choices"][0]["message"]["content"])
            
            # 使用トークン記録
            usage = data.get("usage", {{}})
            database.log_token_usage(QWEN_MODEL, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            
            idx = res.get("best_img_idx", 0)
            res["best_img_url"] = target_imgs[idx] if idx < len(target_imgs) else main_img_url
            return res
    except:
        pass

    return {{ "is_alcohol": False, "is_high_tariff": False, "label": "不明", "best_img_url": main_img_url }}

def estimate_weight_with_llm(img_url, product_name):
    """
    画像と商品名から重量とサイズを推定する。
    """
    prompt = f"""
Product Name: {product_name}
Estimate the weight and physical dimensions (Length x Width x Height) of this product based on the image.
Include units like 'g' or 'kg' for weight, and 'mm' or 'cm' for dimensions.

Output JSON:
{{
  "weight": "estimated weight (e.g. 500g)",
  "dimensions": "L x W x H (e.g. 200 x 150 x 50 mm)"
}}
"""
    res_str = _qwen_post_vision(prompt, img_url)
    if res_str:
        try:
            return json.loads(res_str)
        except:
            pass
    return {{ "weight": "不明", "dimensions": "不明" }}

def verify_model_match(ebay_img_url, domestic_img_url, model_number, domestic_desc, ref_title="", cand_title=""):
    """
    eBayの画像と国内商品の画像/説明/タイトルを比較し、型番が完全に一致しているか判定する。
    """
    prefix_prompt = ""
    if ref_title and cand_title:
        prefix_prompt = f"Reference Title: {ref_title}\nCandidate Title: {cand_title}\n"

    prompt = f"""
Goal: Determine if the TWO products in the images are EXACTLY the same model ({model_number}).
{prefix_prompt}
Model Number to Check: {model_number}
Domestic Product Description: {domestic_desc[:500]}

Comparison Points:
1. Does the model number ({model_number}) appear or is it implied to be the same in the domestic product?
2. Do the colors, shapes, and details in the images match perfectly?
3. Judge the domestic product's condition (New/Used/Mint/etc.).

Output JSON:
{{
  "is_match": bool,
  "condition": "Condition for eBay (e.g. New, Used, Mint, Good, Acceptable)",
  "reason": "Short reason in Japanese"
}}
"""
    # 2枚の画像をQwenに投げる
    b64_ebay = _download_img_b64(ebay_img_url)
    b64_dom  = _download_img_b64(domestic_img_url)
    
    if not b64_ebay or not b64_dom:
        return False, "Good"
        
    payload = {{
        "model": QWEN_MODEL,
        "messages": [
            {{
                "role": "user",
                "content": [
                    {{"type": "text", "text": prompt}},
                    {{"type": "image_url", "image_url": {{ "url": b64_ebay }}}},
                    {{"type": "image_url", "image_url": {{ "url": b64_dom }}}}
                ]
            }}
        ],
        "response_format": {{ "type": "json_object" }}
    }}
    
    headers = {{ "Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json" }}
    
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
        if resp.status_code == 200:
            data = resp.json()
            res = json.loads(data["choices"][0]["message"]["content"])
            
            usage = data.get("usage", {{}})
            database.log_token_usage(QWEN_MODEL, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            
            print(f"    [LLM_VERIFY] Match: {res.get('is_match')}, Cond: {res.get('condition')}, Reason: {res.get('reason')}")
            return res.get("is_match", False), res.get("condition", "Good")
    except:
        pass
        
    return False, "Good"
