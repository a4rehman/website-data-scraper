import json
import os
import re
from typing import Dict, Any, Optional
import requests
import config
from scraper.utils import logger

# Context window limit to prevent token overflow
MAX_INPUT_CHARS = 4000

def parse_ai_json_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Parses JSON output from LLMs, handling markdown block wrappers (```json ... ```),
    trailing commas, or messy string outputs.
    """
    if not raw_text or not isinstance(raw_text, str):
        return None

    cleaned = raw_text.strip()
    
    # Extract contents from markdown codeblock if present
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned, re.IGNORECASE)
    if match:
        cleaned = match.group(1).strip()

    # Attempt direct JSON load
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Regex fallback to find JSON object substring {...}
    json_match = re.search(r'(\{[\s\S]*\})', cleaned)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None

def validate_summary_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensures that parsed AI output complies with expected summary schema.
    Provides safe fallback values for missing keys.
    """
    return {
        "title": str(data.get("title") or "Website Data Summary"),
        "business_type": str(data.get("business_type") or "E-Commerce / Catalog"),
        "summary": str(data.get("summary") or "Catalog extraction completed successfully."),
        "key_highlights": data.get("key_highlights") if isinstance(data.get("key_highlights"), list) else [],
        "product_categories": data.get("product_categories") if isinstance(data.get("product_categories"), list) else [],
        "estimated_catalog_size": str(data.get("estimated_catalog_size") or "N/A"),
    }

def summarize_scraped_data_with_ai(
    products_summary_text: str,
    api_key: Optional[str] = None,
    model: str = "openai/gpt-3.5-turbo"
) -> Dict[str, Any]:
    """
    Summarizes scraped catalog data using OpenRouter/LLM API with safe truncation,
    robust error handling, and JSON validation. Never throws unhandled exceptions.
    """
    key = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    
    if not products_summary_text:
        return {
            "success": False,
            "error": "No product text available for AI analysis.",
            "data": validate_summary_schema({})
        }

    # Token Safety & Truncation (Phase 9)
    truncated_text = products_summary_text[:MAX_INPUT_CHARS]
    if len(products_summary_text) > MAX_INPUT_CHARS:
        truncated_text += "\n...[Content truncated for token safety]"

    if not key:
        logger.info("No AI API key provided. Generating structured fallback summary.")
        fallback_data = {
            "title": "Catalog Summary (Local Rule-Based)",
            "business_type": "E-Commerce Store",
            "summary": f"Extracted product data consisting of {len(products_summary_text.splitlines())} lines.",
            "key_highlights": ["Multiple products processed", "Full pricing & images extracted"],
            "product_categories": [],
            "estimated_catalog_size": f"~{len(products_summary_text.splitlines())} items"
        }
        return {"success": True, "data": validate_summary_schema(fallback_data), "note": "Generated without API key."}

    prompt = (
        "Analyze the following scraped e-commerce catalog sample and return a valid JSON object ONLY.\n"
        "Required JSON schema:\n"
        "{\n"
        '  "title": "Store / Collection Title",\n'
        '  "business_type": "Retail / Clothing / Electronics / etc.",\n'
        '  "summary": "Brief 2-sentence catalog summary",\n'
        '  "key_highlights": ["Highlight 1", "Highlight 2"],\n'
        '  "product_categories": ["Category 1", "Category 2"],\n'
        '  "estimated_catalog_size": "Approximate count or range"\n'
        "}\n\n"
        f"Catalog Content Sample:\n{truncated_text}"
    )

    endpoint = "https://openrouter.ai/api/v1/chat/completions" if "openrouter" in model or "OPENROUTER" in os.environ else "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            res_json = response.json()
            raw_ai_text = res_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            parsed_json = parse_ai_json_response(raw_ai_text)
            if parsed_json:
                valid_data = validate_summary_schema(parsed_json)
                return {"success": True, "data": valid_data}
            else:
                logger.warning("AI returned unparseable JSON text. Using fallback schema.")
                return {
                    "success": False,
                    "error": "AI response was not valid JSON.",
                    "data": validate_summary_schema({"summary": raw_ai_text[:300]})
                }
        elif response.status_code == 401:
            return {"success": False, "error": "Invalid API Key (HTTP 401).", "data": validate_summary_schema({})}
        elif response.status_code == 429:
            return {"success": False, "error": "Rate limit exceeded (HTTP 429).", "data": validate_summary_schema({})}
        else:
            return {"success": False, "error": f"AI API Error (HTTP {response.status_code}).", "data": validate_summary_schema({})}

    except requests.exceptions.Timeout:
        return {"success": False, "error": "AI API request timed out.", "data": validate_summary_schema({})}
    except Exception as e:
        logger.error(f"Unexpected AI processing error: {e}")
        return {"success": False, "error": f"AI processing failed: {e}", "data": validate_summary_schema({})}
