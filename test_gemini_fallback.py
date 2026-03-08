import sys
import os
import requests
from unittest.mock import MagicMock, patch

# Ensure the project directory is in the path
sys.path.append(os.getcwd())

import llm_vision_judge

def test_gemini_fallback():
    # Test data
    test_img_url = "https://i.ebayimg.com/images/g/H6YAAOSw2e9m8m7V/s-l1600.jpg"
    
    # Mocking requests.post to simulate a 429 error from OpenRouter
    with patch('requests.post') as mock_post:
        # First call (OpenRouter) returns 429
        # Second call (Gemini API) returns 200
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'text': '{"is_alcohol": false, "is_high_tariff": true, "material_label": "Metal"}'
                    }]
                }
            }]
        }
        
        # side_effect to return 429 then 200
        mock_post.side_effect = [mock_response_429, mock_response_200]
        
        # Mocking requests.get for image download in Gemini fallback
        with patch('requests.get') as mock_get:
            mock_img_resp = MagicMock()
            mock_img_resp.status_code = 200
            mock_img_resp.content = b"fake_image_data"
            mock_img_resp.headers = {'Content-Type': 'image/jpeg'}
            mock_get.return_value = mock_img_resp
            
            print("[*] Testing Gemini Fallback...")
            result = llm_vision_judge.analyze_item_safety_and_tariff(test_img_url)
            print(f"[*] Result: {result}")
            
            # Assertions
            assert result['is_high_tariff'] == True
            assert result['label'] == "Metal"
            print("[+] Test Passed!")

if __name__ == "__main__":
    test_gemini_fallback()
