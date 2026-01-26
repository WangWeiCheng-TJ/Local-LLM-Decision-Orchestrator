import os
import json
import re
import time
import typing
import google.generativeai as genai
from tqdm import tqdm
from termcolor import colored

import re

def parse_gemma_tags(raw_text):
    # 抓取所有被 @@@ 包裹的區塊
    blocks = re.findall(r'@@@(.*?)@@@', raw_text, re.DOTALL)
    results = []
    
    for block in blocks:
        item = {}
        # 用正則表達式把標籤後的內容抓出來
        lines = block.strip().split('\n')
        for line in lines:
            if ':' in line:
                key, val = line.split(':', 1)
                item[key.strip().lower()] = val.strip()
        results.append(item)
    return results

# ==============================================================================
# Helper Functions: JSON Extraction & Repair
# ==============================================================================

def extract_json_from_text(text: str) -> str:
    """Extracts JSON string from markdown code blocks or raw text."""
    match = re.search(r'```json\s*(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        return text[start : end + 1]
    
    return text.strip()

def aggressive_fix_json(bad_json: str) -> dict:
    """Attempts to fix common LLM JSON syntax errors."""
    try:
        fixed = re.sub(r',\s*}', '}', bad_json)
        fixed = re.sub(r',\s*]', ']', fixed)
        return json.loads(fixed)
    except:
        pass

    try:
        if bad_json.count('{') > bad_json.count('}'):
            fixed = bad_json + '}' * (bad_json.count('{') - bad_json.count('}'))
            return json.loads(fixed)
        if bad_json.count('[') > bad_json.count(']'):
            fixed = bad_json + ']' * (bad_json.count('[') - bad_json.count(']'))
            return json.loads(fixed)
    except:
        pass
        
    return None

def normalize_structure(data):
    """
    Nuclear Normalization V5: Deep Sanitization & Structure Repair.
    """
    if not isinstance(data, (dict, list)):
        return {"error": "Invalid format", "raw": str(data)}

    # === Helper: Identity Checks ===
    def looks_like_gap_item(item):
        if not isinstance(item, dict): return False
        keywords = ["effort", "evidence", "strategy", "gap", "status", "level", "current_state", "specific_gaps"]
        return any(k in item for k in keywords)

    def looks_like_skill_item(item):
        if not isinstance(item, dict): return False
        return ("skill" in item or "topic" in item or "name" in item) and ("priority" in item or "category" in item or "analysis" in item)

    # === Helper: Item Repair (Fix nested types) ===
    def repair_skill_item(item):
        if not isinstance(item, dict): return item
        
        # 1. Fix 'analysis' being a string instead of object
        if "analysis" in item and not isinstance(item["analysis"], dict):
            raw_text = str(item["analysis"])
            item["analysis"] = {
                "hidden_bar": raw_text,
                "quote_from_jd": "Implied from context"
            }
        
        # 2. Ensure 'analysis' exists
        if "analysis" not in item:
             item["analysis"] = {
                "hidden_bar": "Auto-generated analysis",
                "quote_from_jd": "None"
            }
        return item

    # === Helper: Recursive Search ===
    def search_for_list(obj):
        if isinstance(obj, list) and len(obj) > 0:
            if looks_like_gap_item(obj[0]): return obj, "gap"
            if looks_like_skill_item(obj[0]): return obj, "skill"
            if "name" in obj[0] and "specific_gaps" in obj[0]: return obj, "e2_style" 
            return obj, "unknown"

        if isinstance(obj, dict):
            gap_keys = ["gap_analysis", "areas", "specific_gaps", "breakdown"]
            skill_keys = ["required_skills", "skills", "extraction", "requirements"]
            
            for key in gap_keys + skill_keys:
                if key in obj and isinstance(obj[key], list) and len(obj[key]) > 0:
                    return search_for_list(obj[key])

            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    res, type_ = search_for_list(value)
                    if res: return res, type_
        
        return None, None

    # === Execution Logic ===
    found_list, type_ = search_for_list(data)

    if found_list:
        if type_ == "skill":
            sanitized_list = [repair_skill_item(item) for item in found_list]
            return {"required_skills": sanitized_list}
        
        elif type_ == "e2_style":
            normalized_gaps = []
            for item in found_list:
                normalized_gaps.append({
                    "topic": item.get("name", "Unknown"),
                    "effort_assessment": {
                        "level": item.get("effort_estimate", "MEDIUM").upper(),
                        "strategy": "Review specific gaps",
                        "estimated_action": "Manual Check"
                    },
                    "evidence_in_personal_db": {
                        "status": "NOT_FOUND" if item.get("current_state") == "Needs Improvement" else "FOUND_WEAK",
                        "evidence_snippet": str(item.get("specific_gaps", []))
                    },
                    "resume_reusability": {"status": "NO_MATCH", "closest_existing_bullet": None}
                })
            return {"gap_analysis": normalized_gaps}

        elif type_ == "gap":
            return {"gap_analysis": found_list}
            
        else:
            if isinstance(data, dict) and "required_skills" in data:
                 sanitized_list = [repair_skill_item(item) for item in found_list]
                 return {"required_skills": sanitized_list}
            if isinstance(data, dict) and "gap_analysis" in data:
                 return {"gap_analysis": found_list}
            
            if len(found_list) > 0 and isinstance(found_list[0], dict) and "priority" in found_list[0]:
                sanitized_list = [repair_skill_item(item) for item in found_list]
                return {"required_skills": sanitized_list}
                
            return {"gap_analysis": found_list}

    if isinstance(data, dict):
        if looks_like_gap_item(data): return {"gap_analysis": [data]}
        if looks_like_skill_item(data): 
            return {"required_skills": [repair_skill_item(data)]}
    
    return data


# ==============================================================================
# Main Class: SmartModelGateway (Renamed)
# ==============================================================================

class SmartModelGateway:
    def __init__(self, config):
        self.config = {}

        # === [FIX] 聰明的設定檔判斷 ===
        if isinstance(config, dict):
            # Case 1: 傳入的是字典 (標準用法)
            self.config = config

        elif isinstance(config, str):
            # Case 2: 傳入的是字串 (可能是路徑，也可能是 Key)
            if os.path.isfile(config):
                # A. 是一個存在的檔案 -> 讀取它
                tqdm.write(colored(f"  📂 Loading config from file: {config}", "cyan"))
                try:
                    with open(config, 'r') as f:
                        self.config = json.load(f)
                except Exception as e:
                    raise ValueError(f"❌ Failed to load config file: {e}")
            else:
                # B. 不是檔案 -> 認定它是 Raw API Key
                # (只印長度不印內容，保護你的 Key)
                tqdm.write(colored(f"  🔑 Detected raw API Key input (len={len(config)}). Auto-wrapping...", "cyan"))
                self.config = {"api_key": config}
        
        else:
            raise ValueError(f"❌ Invalid config type: {type(config)}. Expected dict or str (path/key).")

        # === 檢查是否拿到 Key ===
        if "api_key" not in self.config:
             raise ValueError("❌ SmartModelGateway Init Failed: Missing 'api_key'.")

        genai.configure(api_key=self.config["api_key"])
        
        # Load Model Names from Env
        lt_name = os.getenv("MODEL_LT_NAME", "gemini-1.5-flash")
        main_name = os.getenv("MODEL_NAME", "gemma-3-27b-it")

        tqdm.write(colored(f"  🤖 SmartModelGateway Init: LT={lt_name}, Main={main_name}", "cyan"))
        
        self.flash_model = genai.GenerativeModel(lt_name)
        self.gemma_model = genai.GenerativeModel(main_name)

    def generate(self, prompt: str, schema_model=None, use_gemma=False) -> dict:
        """
        Public interface for generation.
        """
        model = self.gemma_model if use_gemma else self.flash_model
        
        def validate(data):
            if not schema_model:
                return True, ""
            try:
                schema_model(**data)
                return True, ""
            except Exception as e:
                return False, str(e)

        gen_config = genai.types.GenerationConfig(
            temperature=0.2 if use_gemma else 0.1
        )

        return self._generate_with_retry_logic(
            model=model,
            prompt=prompt,
            validator_func=validate,
            max_retries=3,
            generation_config=gen_config
        )

    def _generate_with_retry_logic(self, model, prompt, validator_func, max_retries, generation_config=None):
        current_prompt = prompt
        last_result = None
        last_error_msg = "Unknown Error"
        system_reminder = "\n\n[SYSTEM]: Return raw JSON only."
        
        # === [FIX 1] Correct Log Path ===
        log_dir = "data"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "debug_gemma.log")

        for attempt in range(max_retries + 1):
            try:
                response = model.generate_content(
                    current_prompt + (system_reminder if attempt > 0 else ""),
                    generation_config=generation_config
                )
                
                raw_text = response.text if response.text else "[EMPTY RESPONSE]"
                
                tqdm.write(colored(f"\n👀 [DEBUG Preview] Attempt {attempt+1}:", "cyan"))
                tqdm.write(colored(raw_text[:200] + "...", "white", attrs=['dark'])) 
                
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n\n{'='*20} ATTEMPT {attempt+1} ({time.strftime('%H:%M:%S')}) {'='*20}\n")
                    f.write(f"--- PROMPT TAIL (Last 300 chars) ---\n...{current_prompt[-300:]}\n")
                    f.write(f"--- RAW RESPONSE ---\n{raw_text}\n")
                    f.write(f"{'='*50}\n")

                if not response.text:
                    raise ValueError("Empty response from API")

                cleaned_text = extract_json_from_text(response.text)
                
                try:
                    result_json = json.loads(cleaned_text)
                except json.JSONDecodeError:
                    result_json = aggressive_fix_json(cleaned_text)
                    if result_json is None:
                        raise ValueError("JSON parse failed (Fix failed)")

                if not isinstance(result_json, (dict, list)):
                    if isinstance(result_json, str):
                        try:
                            result_json = json.loads(result_json)
                        except:
                            pass
                    if not isinstance(result_json, (dict, list)):
                        raise ValueError(f"Parsed result is not JSON object: {type(result_json)}")

                result_json = normalize_structure(result_json)
                last_result = result_json 
                
                is_valid, error_msg = validator_func(result_json)
                
                if is_valid:
                    if attempt > 0:
                        tqdm.write(colored(f"  ✨ Auto-repaired on attempt {attempt+1}", "yellow"))
                    return result_json
                
                last_error_msg = error_msg
                tqdm.write(colored(f"  ⚠️ Validation failed (Attempt {attempt+1}): {error_msg}", "light_red"))
                
                if isinstance(result_json, dict):
                    tqdm.write(colored(f"     Found Keys: {list(result_json.keys())}", "grey"))

                if attempt < max_retries:
                    current_prompt += f"\n\n[SYSTEM ERROR]: {error_msg}. Return strictly valid JSON matching the schema."
                    
                    # === [FIX 2] Aggressive Backoff Sleep ===
                    wait_time = 20 * (attempt + 1)
                    tqdm.write(colored(f"  ⏳ Cooling down for {wait_time}s...", "yellow"))
                    time.sleep(wait_time)

            except Exception as e:
                last_error_msg = str(e)
                tqdm.write(colored(f"  ❌ Error (Attempt {attempt+1}): {e}", "red"))
                
                # Sleep on exception too
                if attempt < max_retries:
                    wait_time = 20 * (attempt + 1)
                    time.sleep(wait_time)

        tqdm.write(colored(f"  💀 DEAD: Final failure due to: {last_error_msg}", "red", attrs=['bold']))
        
        return {
            "error": "Max retries reached", 
            "failure_reason": last_error_msg,
            "gap_analysis": [], 
            "required_skills": [], 
            "debug_dump": {
                "last_attempt_json": last_result,
                "raw_snippet": raw_text[:500] if 'raw_text' in locals() else "No Output"
            }
        }