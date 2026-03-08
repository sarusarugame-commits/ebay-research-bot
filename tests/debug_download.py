import requests
from io import BytesIO
from PIL import Image

url = "https://m.media-amazon.com/images/I/61-P3f6sP1L._AC_SL1500_.jpg"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

try:
    response = requests.get(url, headers=headers, stream=True, timeout=10)
    print(f"Status Code: {response.status_code}")
    response.raise_for_status()
    img = Image.open(BytesIO(response.content))
    print(f"Image format: {img.format}, Size: {img.size}")
except Exception as e:
    print(f"Error: {e}")
