import requests
import json
import base64
import re
import time
import os
import datetime
import functools
import unicodedata
import sys
import select # ユーザー入力待機用

# printを即時出力（バッファリング無効）にする
print = functools.partial(print, flush=True)

try:
    import google.generativeai as genai
except ImportError:
    genai = None

from config import OPENROUTER_API_KEY, GEMINI_API_KEY

if GEMINI_API_KEY and genai:
    genai.configure(api_key=GEMINI_API_KEY)

def show_os_notification(title, message):
    """OS通知を表示する（Windows想定）"""
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name='eBay Research Bot',
            timeout=10
        )
    except Exception:
        # plyerがない場合はPowerShell経由で通知（バルーン通知）
        try:
            os.system(f'powershell -Command "[Reflection.Assembly]::LoadWithPartialName(\'System.Windows.Forms\'); $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $True; $n.ShowBalloonTip(5000, \'{title}\', \'{message}\', [System.Windows.Forms.ToolTipIcon]::Info)"')
        except:
            pass

def wait_for_user_retry():
    """
    ユーザーのエンターキー入力を無期限で待機する。
    """
    print(f"\n[!] タイムアウトまたはエラーが発生しました。")
    print(f"    >>> 再試行するには 【エンターキー】 を押してください（ユーザー入力待ち）...")
    
    show_os_notification("LLM検証エラー", "Geminiが応答しません。再試行するにはターミナルでエンターを押してください。")
    
    start_time = time.time()
    while True:
        elapsed = int(time.time() - start_time)
        sys.stdout.write(f"\r    待機継続中: {elapsed}s 経過 ... (Enterで再試行) ")
        sys.stdout.flush()
        
        # Windows/Posix両対応の非ブロッキング入力待機
        if os.name == 'nt':
            import msvcrt
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key in [b'\r', b'\n']:
                    print("\n    [*] ユーザー入力を受け付けました。再試行を開始します。")
                    return True
        else:
            rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
            if rlist:
                sys.stdin.readline()
                print("\n    [*] ユーザー入力を受け付けました。再試行を開始します。")
                return True
        time.sleep(0.1)

def judge_similarity_with_llm(ebay_img_url, scraped_items):
    """
    OpenRouter (Gemma 3) のVision機能を用いて、
    eBay元画像とスクレイピングしたメルカリ画像を直接比較し、
    同一商品の可能性を0〜100のスコアで返す。
    """
    if not OPENROUTER_API_KEY:
        print("WARNING: OPENROUTER_API_KEYが設定されていないため、LLM画像判定をスキップします。")
        return scraped_items
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/eBayResearchSystem",
        "X-Title": "eBay Research System"
    }

    results = []
    # 最大5件に制限
    items_to_judge = scraped_items[:5]
    print(f"\n[*] LLM (Gemma 3) を用いた画像比較を開始します（合計 {len(items_to_judge)}件）...")

    for item in items_to_judge:
        time.sleep(3)
        
        mercari_img_url = item.get("img_url")
        if not mercari_img_url:
            item["score"] = 0
            results.append(item)
            continue
            
        combined_prompt = (
            "あなたはプロの真贋鑑定士・ECリサーチャーです。以下の指示に従って2つの画像を比較してください。\n\n"
            "【指示】\n"
            "2つの商品画像（画像1: eBayの元画像、画像2: Web検索の候補画像）を比較し、これらが「完全に同一の型番・モデルの商品」である確率を、0から100の数値（スコア）で判定してください。\n"
            "【注意点】\n"
            "- 背景、照明、撮影角度、付属品や箱の有無などは無視してください。\n"
            "- 本体の形状、テクスチャ、ロゴの配置、文字盤のデザインなど「物理的な製品特徴」が一致しているかを厳格に見てください。\n"
            "- 同一製品の別カラーバリエーションは「同一モデル」ではないため、低いスコア（0〜10点）にしてください。\n\n"
            "出力は必ずスコアの数値（例：85）のみとし、文章や理由は一切含めないでください。\n\n"
            "画像1（eBayの元画像）と画像2（候補画像）を比較し、同じ商品モデルである確率のスコアを出力せよ。"
        )
        
        payload = {
            "model": "google/gemma-3-27b-it:free",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": combined_prompt},
                        {"type": "image_url", "image_url": {"url": ebay_img_url}},
                        {"type": "image_url", "image_url": {"url": mercari_img_url}}
                    ]
                }
            ],
            "temperature": 0.0
        }

        try:
            print(f"  -> 画像比較中: {mercari_img_url[:60]}...")
            score = 0
            response = requests.post(url, headers=headers, json=payload, timeout=20)
            if response.status_code == 200:
                result_data = response.json()
                content = result_data["choices"][0]["message"]["content"].strip()
                match = re.search(r'\d+', content)
                score = int(match.group()) if match else 0
                print(f"    [LLM/Gemma] 類似度スコア: {score}")
            elif response.status_code == 429:
                print(f"     [!] Rate limit (429) 検出。Gemini API でリトライします...")
                score = judge_similarity_with_gemini(ebay_img_url, mercari_img_url, combined_prompt)
            else:
                print(f"     [!] エラー: {response.status_code} - {response.text}")
                score = judge_similarity_with_gemini(ebay_img_url, mercari_img_url, combined_prompt)
                
        except Exception as e:
            print(f"     [!] リクエスト失敗: {e}")
            score = judge_similarity_with_gemini(ebay_img_url, mercari_img_url, combined_prompt)

        item["score"] = score
        results.append(item)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results

def judge_similarity_with_gemini(ebay_img_url, mercari_img_url, prompt):
    """画像類似度判定のGeminiフォールバック（リトライ対応）"""
    if not GEMINI_API_KEY: return 0
    
    model = "gemini-3.1-flash-lite-preview"
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    
    # max_retries = 2
    while True:
        try:
            resp1 = requests.get(ebay_img_url, timeout=10)
            resp2 = requests.get(mercari_img_url, timeout=10)
            if resp1.status_code != 200 or resp2.status_code != 200:
                if wait_for_user_retry(): continue
                # ユーザーが中断を選択した場合はスコア0で返す
                return 0
            
            data1 = base64.b64encode(resp1.content).decode('utf-8')
            data2 = base64.b64encode(resp2.content).decode('utf-8')
            mime1 = resp1.headers.get('Content-Type', 'image/jpeg')
            mime2 = resp2.headers.get('Content-Type', 'image/jpeg')

            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime1, "data": data1}},
                        {"inline_data": {"mime_type": mime2, "data": data2}}
                    ]
                }]
            }
            resp = requests.post(api_url, json=payload, timeout=20)
            if resp.status_code == 200:
                text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                match = re.search(r'\d+', text)
                return int(match.group()) if match else 0
            elif resp.status_code == 429:
                print(f"    [LLM/Gemini] 429 Error (Rate Limit)")
                time.sleep(5)
            
            if wait_for_user_retry(): continue
        except Exception as e:
            print(f"    [!] Gemini類似度判定中に例外発生: {e}")
            if wait_for_user_retry(): continue
                
    return 0

def estimate_weight_with_llm(ebay_img_url, final_name):
    """
    Gemma 3 (Vision) を用いて、商品の見た目と名称から「重量(kg)」と「サイズ(cm)」を推論する。
    """
    if not OPENROUTER_API_KEY:
        return {"weight": "不明", "dimensions": "不明"}
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

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

    payload = {
        "model": "google/gemma-3-27b-it:free",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": ebay_img_url}}
                ]
            }
        ],
        "temperature": 0.0
    }

    try:
        print(f"[*] Gemma 3 が画像から重量・サイズを推論しています...")
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                print(f"    [LLM/Gemma] 推論結果: {data}")
                return {
                    "weight": data.get("weight", "不明"),
                    "dimensions": data.get("dimensions", "不明")
                }
            else:
                raise ValueError("JSON not found")
        else:
            print(f"    [!] OpenRouter Error ({response.status_code}): {response.text}")
            print(f"    [*] Gemini API でリトライします...")
            return estimate_weight_with_gemini(ebay_img_url, final_name, prompt)
    except Exception as e:
        print(f"    [!] スペック推論中に例外発生: {e}")
        print(f"    [*] Gemini API でリトライします...")
        return estimate_weight_with_gemini(ebay_img_url, final_name, prompt)

def estimate_weight_with_gemini(ebay_img_url, final_name, prompt):
    """スペック推論のGeminiフォールバック（リトライ対応）"""
    if not GEMINI_API_KEY: return {"weight": "不明", "dimensions": "不明"}
    
    model = "gemini-3.1-flash-lite-preview"
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    
    # max_retries = 2
    while True:
        try:
            print(f"    [*] Geminiスペック推論用画像ダウンロード中...")
            img_resp = requests.get(ebay_img_url, timeout=10)
            if img_resp.status_code != 200: 
                print(f"    [!] 画像ダウンロード失敗: {img_resp.status_code}")
                if wait_for_user_retry(): continue
                return {"weight": "不明", "dimensions": "不明"}
            
            img_data = base64.b64encode(img_resp.content).decode('utf-8')
            mime_type = img_resp.headers.get('Content-Type', 'image/jpeg')

            payload = {
                "contents": [{
                    "parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": img_data}}]
                }]
            }
            print(f"    [*] Gemini API 呼び出し中 (timeout=20)...")
            resp = requests.post(api_url, json=payload, timeout=20)
            
            if resp.status_code == 200:
                res_json = resp.json()
                text_content = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                json_match = re.search(r'\{.*\}', text_content, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group())
                    print(f"    [LLM/Gemini] 推論結果: {data}")
                    return {
                        "weight": data.get("weight", "不明"),
                        "dimensions": data.get("dimensions", "不明")
                    }
            elif resp.status_code == 429:
                print(f"    [LLM/Gemini] 429 Error (Rate Limit)")
                time.sleep(5)
            else:
                print(f"    [LLM/Gemini] API Error ({resp.status_code}): {resp.text}")

            if wait_for_user_retry(): continue
        except Exception as e:
            print(f"    [!] Geminiスペック推論中に例外発生: {e}")
            if wait_for_user_retry(): continue
    
    return {"weight": "不明", "dimensions": "不明"}

def analyze_item_safety_and_tariff(ebay_img_url):
    """
    Gemma 3 (Vision) を用いて、商品の「カテゴリー（アルコールか？）」と
    「素材（鉄・皮製品などの高関税対象か？）」を判定する。
    """
    if not OPENROUTER_API_KEY:
        return {"is_alcohol": False, "is_high_tariff": False, "label": "不明"}
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = (
        "あなたは税関および物流の専門家です。添付された商品の画像を見て、以下の2点を判定してください。\n\n"
        "1. 【アルコール製品か？】 ビール、ワイン、ウィスキー、酒類、またはアルコールを含有する製品（消毒液等含む）であれば 'true'、そうでなければ 'false'。\n"
        "2. 【高関税素材か？】 商品の材質に '金属（Metal）' または '革（Leather）' が含まれるか？ 含まれるなら 'true'、そうでなければ 'false'。\n\n"
        "【出力形式】\n"
        "以下のJSON形式でのみ出力してください（文章は一切不要です）。\n"
        "{\"is_alcohol\": bool, \"is_high_tariff\": bool, \"material_label\": \"見つかった素材名またはnull\"}"
    )

    payload = {
        "model": "google/gemma-3-27b-it:free",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": ebay_img_url}}
                ]
            }
        ],
        "temperature": 0.0
    }

    try:
        print(f"[*] Gemma 3 が商品の安全性をチェックしています（アルコール/高関税素材）...")
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                print(f"    [LLM/Gemma] 安全性判定結果: {data}")
                return {
                    "is_alcohol": data.get("is_alcohol", False),
                    "is_high_tariff": data.get("is_high_tariff", False),
                    "label": data.get("material_label", "なし")
                }
            else:
                raise ValueError("JSON not found")
        else:
            print(f"     [!] OpenRouter Error ({response.status_code}): {response.text}")
            print(f"     [*] Gemini API で判定をリトライします...")
            return analyze_item_safety_with_gemini(ebay_img_url, prompt)
            
    except Exception as e:
        print(f"     [!] 安全性チェック中に例外発生: {e}")
        print(f"     [*] Gemini API で判定をリトライします...")
        return analyze_item_safety_with_gemini(ebay_img_url, prompt)

def analyze_item_safety_with_gemini(ebay_img_url, prompt):
    """安全性判定のGeminiフォールバック"""
    if not GEMINI_API_KEY:
        return {"is_alcohol": False, "is_high_tariff": False, "label": "不明"}

    model = "gemini-3.1-flash-lite-preview"
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"

    # max_retries = 2
    while True:
        try:
            print(f"    [*] Gemini安全性判定用画像ダウンロード中...")
            r = requests.get(ebay_img_url, timeout=10)
            if r.status_code != 200:
                print(f"    [!] 画像ダウンロード失敗: {r.status_code}")
                if wait_for_user_retry(): continue
                return {"is_alcohol": False, "is_high_tariff": False, "label": "不明"}

            img_data = base64.b64encode(r.content).decode('utf-8')
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": img_data}}]
                }]
            }
            print(f"    [*] Gemini API 呼び出し中 (timeout=20)...")
            resp = requests.post(api_url, json=payload, timeout=20)
            if resp.status_code == 200:
                res_json = resp.json()
                text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                json_match = re.search(r'\{.*\}', text, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group())
                    print(f"    [LLM/Gemini] 安全性判定結果: {data}")
                    return {
                        "is_alcohol": bool(data.get("is_alcohol", False)),
                        "is_high_tariff": bool(data.get("is_high_tariff", False)),
                        "label": data.get("material_label", "なし")
                    }
            elif resp.status_code == 429:
                print(f"    [LLM/Gemini] 429 Error (Rate Limit)")
                time.sleep(5)
            else:
                print(f"    [LLM/Gemini] API Error ({resp.status_code}): {resp.text}")
            
            if wait_for_user_retry(): continue
        except Exception as e:
            print(f"    [LLM/Gemini] 例外発生: {e}")
            if wait_for_user_retry(): continue

    return {"is_alcohol": False, "is_high_tariff": False, "label": "不明"}

def verify_model_match(ref_img_url, candidate_img_url, model_number, condition_text):
    """
    LLM (Gemma 3 Vision) を用いて、2つの商品画像が同一型番かどうかを判定し、
    同時に国内の商品説明から eBay のコンディションを判定する。
    """
    if not OPENROUTER_API_KEY:
        print("    [WARN] OPENROUTER_API_KEY未設定のためLLM型番判定をスキップ（通過扱い）")
        return True, "Good"

    prompt = f"""
    あなたはプロのeBayセラー兼鑑定士です。以下の2枚の画像を比較し、正確な鑑定を行ってください。

    【鑑定する型番/モデル】: {model_number}

    【国内サイトの商品説明テキスト】:
    {condition_text}

    【タスク1: 同一性判定】
    画像1（eBay参照画像）と画像2（国内候補画像）を比較し、これらが「完全に同じ型番・モデル・カラー」であるか判定してください。
    - ロゴの配置、文字盤のデザイン、ベゼルの形状、ボタンの位置、独特な模様などを細部まで確認すること。
    - 色違いや世代違いは "match": false としてください。

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

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "google/gemma-3-27b-it:free",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": ref_img_url}},
                {"type": "image_url", "image_url": {"url": candidate_img_url}}
            ]
        }],
        "temperature": 0.0
    }

    try:
        print(f"    [LLM] 型番一致・状態判定中: {model_number}...")
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                is_match = bool(data.get("match", False))
                ebay_condition = data.get("condition", "Good")
                reason = data.get("reason", "不明")
                
                label = "✅ 一致" if is_match else "❌ 不一致"
                print(f"    [LLM] {label} | 判定状態: {ebay_condition} | 理由: {reason}")
                return is_match, ebay_condition
            else:
                raise ValueError("JSON not found")
        else:
            print(f"    [LLM] OpenRouter Error ({resp.status_code}) 詳細: {resp.text}")
            print(f"    [*] Gemini API でリトライします...")
            return _verify_model_match_with_gemini(ref_img_url, candidate_img_url, prompt)
    except Exception as e:
        print(f"    [LLM] 型番判定中に例外発生: {e}")
        print(f"    [*] Gemini API でリトライします...")
        return _verify_model_match_with_gemini(ref_img_url, candidate_img_url, prompt)

def _verify_model_match_with_gemini(ref_img_url, candidate_img_url, prompt):
    """verify_model_matchのGeminiフォールバック（requests使用）"""
    if not GEMINI_API_KEY:
        return True, "Good"
        
    model = "gemini-3.1-flash-lite-preview"
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    
    # max_retries = 2
    while True:
        try:
            def download_img(url):
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    return base64.b64encode(r.content).decode('utf-8')
                return None

            print(f"    [*] Gemini型番検証用画像ダウンロード中...")
            data1 = download_img(ref_img_url)
            data2 = download_img(candidate_img_url)

            if not data1 or not data2:
                print(f"    [LLM/Gemini] 画像ダウンロード失敗")
                if wait_for_user_retry(): continue
                return True, "Good"

            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/jpeg", "data": data1}},
                        {"inline_data": {"mime_type": "image/jpeg", "data": data2}}
                    ]
                }]
            }
            
            print(f"    [*] Gemini API 呼び出し中 (timeout=20)...")
            resp = requests.post(api_url, json=payload, timeout=20)
            
            if resp.status_code == 200:
                res_json = resp.json()
                text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                json_match = re.search(r'\{.*\}', text, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group())
                    is_match = bool(data.get("match", False))
                    ebay_condition = data.get("condition", "Good")
                    reason = data.get("reason", "不明")
                    
                    label = "✅ 一致" if is_match else "❌ 不一致"
                    print(f"    [LLM/Gemini] {label} | 判定状態: {ebay_condition} | 理由: {reason}")
                    return is_match, ebay_condition
                print(f"    [LLM/Gemini] JSONパース失敗")
            elif resp.status_code == 429:
                print(f"    [LLM/Gemini] 429 Error (Rate Limit)")
                time.sleep(5)
            else:
                print(f"    [LLM/Gemini] API Error ({resp.status_code}): {resp.text}")

            if wait_for_user_retry(): continue

        except Exception as e:
            print(f"    [LLM/Gemini] 例外発生: {e}")
            if wait_for_user_retry(): continue
                
    return True, "Good"
