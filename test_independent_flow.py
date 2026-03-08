import torch
from clip_judge import load_and_remove_bg, rgba_to_rgb_white_bg, get_dino_embeddings, get_color_gate_score, get_dino_score

def test_independent_flow():
    ebay_url = "https://i.ebayimg.com/images/g/Y8IAAOSwHhplz~i~/s-l1600.jpg"
    candidates = [
        {"img_url": "https://i.ebayimg.com/images/g/Y8IAAOSwHhplz~i~/s-l1600.jpg", "title": "Same Image"},
        {"img_url": "https://m.media-amazon.com/images/I/71ovN4+L2iL._AC_SL1500_.jpg", "title": "Different Image (Mouse)"}
    ]

    print("[*] Initializing reference...")
    ebay_rgba = load_and_remove_bg(ebay_url)
    ebay_rgb = rgba_to_rgb_white_bg(ebay_rgba)
    ebay_emb = get_dino_embeddings([ebay_rgb])[0].unsqueeze(0)

    print("[*] Running Color Gate...")
    color_results = get_color_gate_score(ebay_rgba, candidates)
    for res in color_results:
        print(f"  - {res['title']}: Color Score = {res.get('color_score'):.1f}")

    print("[*] Running DINO Score...")
    dino_results = get_dino_score(ebay_emb, color_results)
    for res in dino_results:
        print(f"  - {res['title']}: DINO Score = {res.get('dino_score'):.1f}")

if __name__ == "__main__":
    test_independent_flow()
