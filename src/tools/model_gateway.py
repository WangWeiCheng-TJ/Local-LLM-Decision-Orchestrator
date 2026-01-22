import time
import json
import re
import ast
import google.generativeai as genai
from termcolor import colored, cprint
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, InternalServerError, Aborted
from tqdm import tqdm

# === JSON 處理工具 ===
def extract_json_from_text(text):
    """從 AI 回覆中提取 JSON 區塊"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match: return match.group(1)
    match_list = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if match_list: return match_list.group(1)
    
    # 嘗試找最外層的括號
    text = text.strip()
    start_brace = text.find('{')
    start_bracket = text.find('[')
    
    if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
        end = text.rfind('}')
        if end > start_brace: return text[start_brace:end+1]
    elif start_bracket != -1:
        end = text.rfind(']')
        if end > start_bracket: return text[start_bracket:end+1]
    return text

def aggressive_fix_json(bad_json_str):
    """暴力修復 JSON 格式錯誤"""
    try:
        py_str = bad_json_str.replace("null", "None").replace("true", "True").replace("false", "False")
        return ast.literal_eval(py_str)
    except:
        pass
    try:
        fixed = bad_json_str.replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null")
        return json.loads(fixed)
    except:
        return None

def normalize_structure(data):
    """
    終極結構正規化：暴力遞迴尋找目標資料
    解決 Gemma 不支援 Pydantic Schema 導致的 Key 名稱亂飄問題 (assessment, output, etc.)
    """
    
    # 定義我們在找什麼 (特徵指紋)
    # Gap Analysis 的特徵：是一個 list，且裡面的 dict 包含 'effort_assessment' 或 'evidence_in_personal_db'
    def looks_like_gap_item(item):
        return isinstance(item, dict) and (
            "effort_assessment" in item or 
            "evidence_in_personal_db" in item or 
            "resume_reusability" in item or
            "strategy" in item  # 有時候 Gemma 會直接寫 strategy
        )

    # Skill Extraction 的特徵：是一個 list，且裡面的 dict 包含 'skill' 或 'priority'
    def looks_like_skill_item(item):
        return isinstance(item, dict) and ("skill" in item or "priority" in item)

    # === 策略 A: 根目錄就是 List ===
    if isinstance(data, list):
        if not data: return {"required_skills": [], "gap_analysis": []}
        if looks_like_gap_item(data[0]): return {"gap_analysis": data}
        if looks_like_skill_item(data[0]): return {"required_skills": data}
        return {"required_skills": data} # Fallback

    # === 策略 B: 根目錄是 Dict，搜尋所有 Key ===
    if isinstance(data, dict):
        # 1. 完美情況 (Pydantic 生效)
        if "gap_analysis" in data and isinstance(data["gap_analysis"], list):
            return data
        if "required_skills" in data and isinstance(data["required_skills"], list):
            return data

        # 2. 模糊搜尋 (Gemma 亂取名)
        # 我們遍歷 Dict 的每一個 value，看誰是我們要在找的 List
        found_gap_list = None
        found_skill_list = None

        for key, value in data.items():
            if isinstance(value, list) and len(value) > 0:
                if looks_like_gap_item(value[0]):
                    found_gap_list = value
                elif looks_like_skill_item(value[0]):
                    found_skill_list = value
            
            # 特殊情況：Gemma 有時候會包一層 {"assessment": {"items": [...]}}
            elif isinstance(value, dict):
                # 遞迴檢查下一層 (只找一層，避免無限遞迴)
                normalized_sub = normalize_structure(value)
                if "gap_analysis" in normalized_sub and normalized_sub["gap_analysis"]:
                    found_gap_list = normalized_sub["gap_analysis"]
                if "required_skills" in normalized_sub and normalized_sub["required_skills"]:
                    found_skill_list = normalized_sub["required_skills"]

        # 3. 重組回傳
        if found_gap_list:
            return {"gap_analysis": found_gap_list}
        if found_skill_list:
            return {"required_skills": found_skill_list}

        # 4. 如果真的找不到 List，嘗試看單一物件
        # 有時候 Gemma 忘記包 List，直接回傳單個 Dict
        if looks_like_gap_item(data):
            return {"gap_analysis": [data]}
        if looks_like_skill_item(data):
            return {"required_skills": [data]}

    return data

# ==========================================
# 🚀 SmartModelGateway
# ==========================================
class SmartModelGateway:
    def __init__(self, api_key, token_threshold=14000):
        if not api_key:
            raise ValueError("API Key is missing!")
            
        genai.configure(api_key=api_key)
        self.token_threshold = token_threshold
        
        # Models
        self.smart_model_name = "gemma-3-27b-it" # or gemini-2.5-flash
        self.smart_model = genai.GenerativeModel(self.smart_model_name)
        
        self.fast_model_name = "gemini-2.5-flash"
        self.fast_model = genai.GenerativeModel(self.fast_model_name)

        cprint(f"🤖 Gateway Init: {self.smart_model_name} / {self.fast_model_name}", "green")

    def _select_model(self, prompt):
        try:
            count_res = self.smart_model.count_tokens(prompt)
            total_tokens = count_res.total_tokens
        except Exception as e:
            cprint(f"⚠️ Token counting failed: {e}. Defaulting to Flash.", "yellow")
            return self.fast_model, 999999

        if total_tokens > self.token_threshold:
            cprint(f"  💎 Load: {total_tokens} > {self.token_threshold}. Using PRECIOUS quota ({self.fast_model_name})", "cyan", attrs=['bold'])
            return self.fast_model, total_tokens
        else:
            cprint(f"  🧠 Load: {total_tokens} < {self.token_threshold}. Using UNLIMITED quota ({self.smart_model_name})", "magenta")
            return self.smart_model, total_tokens

    def generate(self, prompt, validator_func, schema=None, max_retries=2):
        # 1. 決定模型
        selected_model, _ = self._select_model(prompt)
        
        # 2. 準備 Generation Config (Pydantic Support)
        generation_config = {}
        
        # 只有 Flash 且有 schema 時啟用 structured output
        if schema and "flash" in selected_model.model_name.lower():
            generation_config = genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=schema
            )
        
        return self._generate_with_retry_logic(selected_model, prompt, validator_func, max_retries, generation_config)

    def _generate_with_retry_logic(self, model, prompt, validator_func, max_retries, generation_config=None):
        current_prompt = prompt
        last_result = None
        # 如果用了 schema，通常不需要 system reminder，但為了保險還是留著
        system_reminder = "\n\n[SYSTEM]: Return raw JSON only."

        for attempt in range(max_retries + 1):
            try:
                # 呼叫 API
                response = model.generate_content(
                    current_prompt + (system_reminder if attempt > 0 else ""),
                    generation_config=generation_config
                )
                
                # [FIXED] 確保這裡有定義 cleaned_text
                # 即使是 Structured Output，有時候 API 還是會沒回傳東西或格式怪怪的
                if not response.text:
                    raise ValueError("Empty response from API")

                cleaned_text = extract_json_from_text(response.text)
                
                # 解析 JSON
                try:
                    result_json = json.loads(cleaned_text)
                except json.JSONDecodeError:
                    result_json = aggressive_fix_json(cleaned_text)
                    if result_json is None:
                        # 最後一搏：如果真的爛掉，且沒有 schema，才報錯
                        # 如果有 schema，通常 cleaned_text 本身就是標準 JSON
                        raise json.JSONDecodeError("Fix failed", cleaned_text, 0)
                
                # 結構正規化
                result_json = normalize_structure(result_json)
                last_result = result_json
                
                # 驗證內容
                is_valid, error_msg = validator_func(result_json)
                if is_valid:
                    if attempt > 0:
                        tqdm.write(colored(f"  ✨ Auto-repaired on attempt {attempt+1}", "yellow"))
                    return result_json
                
                tqdm.write(colored(f"  ⚠️ Validation failed (Attempt {attempt+1}): {error_msg}", "light_red"))
                if attempt < max_retries:
                    current_prompt += f"\n\n[SYSTEM ERROR]: {error_msg}."
                    time.sleep(2)

            except ResourceExhausted as e:
                tqdm.write(colored(f"  💀 QUOTA EXCEEDED (429). Limit reached for {model.model_name}.", "red", attrs=['bold', 'reverse']))
                raise e 

            except (ServiceUnavailable, InternalServerError, Aborted) as e:
                tqdm.write(colored(f"  🔥 Server Error ({e.code}). Retrying in 10s...", "red"))
                time.sleep(10)

            except Exception as e:
                tqdm.write(colored(f"  ❌ System Error: {e}", "red"))
                time.sleep(5)

        return last_result or {"error": "Max retries reached"}