import os
import requests

# Test DeepSeek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

def test_deepseek():
    """Test if DeepSeek API is working"""
    
    if not DEEPSEEK_API_KEY:
        return {
            "success": False,
            "error": "DEEPSEEK_API_KEY not found in environment variables"
        }
    
    print(f"API Key: {DEEPSEEK_API_KEY[:10]}... (hidden)")
    
    # Simple test prompt
    prompt = """You are a football analyst. Analyze this match:

Manchester City vs Liverpool
Premier League

Return ONLY valid JSON:
{
  "prediction": "HOME_WIN",
  "confidence": 75,
  "reasoning": "Test analysis",
  "recommended_bet": "Home Win @2.30",
  "risk_level": "LOW",
  "expected_value": 1.15
}
"""
    
    try:
        print("Sending request to DeepSeek API...")
        
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 500
            },
            timeout=30
        )
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 401:
            return {
                "success": False,
                "error": "Authentication failed - API key is invalid or expired",
                "status_code": 401
            }
        
        if response.status_code == 429:
            return {
                "success": False,
                "error": "Rate limit exceeded - too many requests",
                "status_code": 429
            }
        
        response.raise_for_status()
        result = response.json()
        
        print("Response received!")
        print(f"Full response: {result}")
        
        # Parse AI response
        import json
        ai_response = result["choices"][0]["message"]["content"]
        ai_response = ai_response.replace('```json', '').replace('```', '').strip()
        
        prediction_data = json.loads(ai_response)
        
        return {
            "success": True,
            "message": "DeepSeek API is working!",
            "prediction": prediction_data.get('prediction'),
            "confidence": prediction_data.get('confidence'),
            "reasoning": prediction_data.get('reasoning'),
            "full_response": ai_response
        }
        
    except requests.exceptions.HTTPError as e:
        return {
            "success": False,
            "error": f"HTTP Error: {e}",
            "status_code": response.status_code if 'response' in locals() else None,
            "response_text": response.text if 'response' in locals() else None
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error: {str(e)}",
            "error_type": type(e).__name__
        }

if __name__ == "__main__":
    print("\n" + "="*60)
    print("DEEPSEEK API TEST")
    print("="*60 + "\n")
    
    result = test_deepseek()
    
    print("\n" + "="*60)
    print("RESULT:")
    print("="*60)
    
    if result["success"]:
        print("✅ SUCCESS!")
        print(f"Prediction: {result['prediction']}")
        print(f"Confidence: {result['confidence']}%")
        print(f"Reasoning: {result['reasoning']}")
    else:
        print("❌ FAILED!")
        print(f"Error: {result['error']}")
        if 'status_code' in result:
            print(f"Status Code: {result['status_code']}")
        if 'response_text' in result:
            print(f"Response: {result['response_text']}")
    
    print("="*60 + "\n")
