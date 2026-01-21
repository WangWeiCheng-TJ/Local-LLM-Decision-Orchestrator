import time
import json
import re
import ast
import os
import google.generativeai as genai
from termcolor import colored, cprint
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, InternalServerError, Aborted
from tqdm import tqdm

MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-pro")
MODEL_LT_NAME = os.getenv("MODEL_LT_NAME", "gemini-1.5-pro")

# ==========================================
# 🔧 輔助工具：JSON 清潔與正規化
# ==========================================

def extract_json_from_text(text):
    """從 LLM 回傳的混雜文字中提取 JSON區塊"""
    # 1. 嘗試抓 Markdown ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match: return match.group(1)
    
    match_list = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if match_list: return match_list.group(1)

    # 2. 嘗試抓最外層的 { } 或 [ ]
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
    """暴力修復不標準的 JSON (處理單引號、Python None/True/False)"""
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
    """標準化 JSON 結構，確保不管是 List 還是 Dict 都能轉成標準格式"""
    # 1. 處理 List (Gemma 很愛直接回傳 List)
    if isinstance(data, list):
        if not data: return {"required_skills": [], "gap_analysis": []} 
        
        # 偷看第一筆資料來決定是哪種模式
        first_item = data[0]
        if "effort_assessment" in first_item or "evidence_in_personal_db" in first_item:
            return {"gap_analysis": data}
        else:
            return {"required_skills": data}

    # 2. 處理 Dict (修正各種奇怪的 Key 名稱)
    if isinstance(data, dict):
        # Skill 模式的別名修正
        wrong_skill_keys = ["skills", "requirements", "extraction", "output", "result", "items", "skills_and_requirements", "required_skills_list"]
        for key in wrong_skill_keys:
            if key in data and isinstance(data[key], list):
                data["required_skills"] = data.pop(key)
                
        # Gap 模式的別名修正
        wrong_gap_keys = ["gaps", "analysis", "assessment", "gap_report", "gap_analysis_list"]
        for key in wrong_gap_keys:
            if key in data and isinstance(data[key], list):
                data["gap_analysis"] = data.pop(key)

        # 單一物件修正 (有些模型只回傳單個 Dict 而不是 List)
        if "topic" in data and "required_skills" not in data and "gap_analysis" not in data:
            if "effort_assessment" in data:
                return {"gap_analysis": [data]}
            else:
                return {"required_skills": [data]}
    return data

# ==========================================
# 🚀 主角：SmartModelGateway
# ==========================================

class SmartModelGateway:
    def __init__(self, api_key, token_threshold=14000):
        if not api_key:
            raise ValueError("API Key is missing!")
            
        genai.configure(api_key=api_key)
        
        # 設定切換閥值 (超過這個 Token 數就切換到 Flash)
        self.token_threshold = token_threshold
        
        # 初始化模型物件
        self.smart_model_name = MODEL_NAME  # 邏輯強，Rate Limit 嚴格
        self.smart_model = genai.GenerativeModel(self.smart_model_name)
        
        self.fast_model_name = MODEL_LT_NAME # 速度快，吞吐量大
        self.fast_model = genai.GenerativeModel(self.fast_model_name)

        cprint(f"🤖 Model Gateway Initialized ({self.smart_model_name} / {self.fast_model_name})", "green")

    def _select_model(self, prompt):
        """[內部方法] 根據 Prompt 長度決定使用哪個模型"""
        try:
            # 優先嘗試用 smart_model 的 tokenizer 算 Token
            count_res = self.smart_model.count_tokens(prompt)
            total_tokens = count_res.total_tokens
        except Exception as e:
            cprint(f"⚠️ Token counting failed: {e}. Defaulting to {MODEL_LT_NAME}.", "yellow")
            return self.fast_model, 999999

        # 決策邏輯
        if total_tokens > self.token_threshold:
            cprint(f"  ⚖️ Load: {total_tokens} > {self.token_threshold}. Switching to ⚡ {self.fast_model_name}", "cyan")
            return self.fast_model, total_tokens
        else:
            cprint(f"  ⚖️ Load: {total_tokens} tokens. Using 🧠 {self.smart_model_name}", "magenta")
            return self.smart_model, total_tokens

    def generate(self, prompt, validator_func, max_retries=2):
        """
        [公開方法] 統一入口：自動路由 + 自動重試 + 自動錯誤處理
        """
        # 1. 決定模型
        selected_model, _ = self._select_model(prompt)
        
        # 2. 執行生成 (包含 Retry 邏輯)
        return self._generate_with_retry_logic(selected_model, prompt, validator_func, max_retries)

    def _generate_with_retry_logic(self, model, prompt, validator_func, max_retries):
        """
        [內部方法] 封裝後的重試邏輯 (原 generate_with_retry)
        """
        current_prompt = prompt
        last_result = None
        system_reminder = "\n\n[SYSTEM]: Return raw JSON only. Use double quotes."

        for attempt in range(max_retries + 1):
            try:
                final_prompt = current_prompt + (system_reminder if attempt > 0 else "")
                
                # 呼叫 API
                response = model.generate_content(final_prompt)
                
                # 解析與正規化
                cleaned_text = extract_json_from_text(response.text)
                try:
                    result_json = json.loads(cleaned_text)
                except json.JSONDecodeError:
                    result_json = aggressive_fix_json(cleaned_text)
                    if result_json is None:
                        raise json.JSONDecodeError("Fix failed", cleaned_text, 0)
                
                result_json = normalize_structure(result_json)
                last_result = result_json
                
                # 驗證
                is_valid, error_msg = validator_func(result_json)
                if is_valid:
                    if attempt > 0:
                        tqdm.write(colored(f"  ✨ Auto-repaired on attempt {attempt+1}", "yellow"))
                    return result_json
                
                # 驗證失敗 (邏輯錯)
                tqdm.write(colored(f"  ⚠️ Validation failed (Attempt {attempt+1}): {error_msg}", "light_red"))
                if attempt < max_retries:
                    current_prompt += f"\n\n[SYSTEM ERROR]: {error_msg}. Check keys."
                    time.sleep(2) # 小錯誤睡一下就好

            # === 錯誤處理區塊 ===
            except ResourceExhausted as e:
                # 429 Error: 指數退避 (Exponential Backoff)
                wait_seconds = 40 * (attempt + 1) # 第一次 40s，第二次 80s
                tqdm.write(colored(f"  🛑 Rate Limit (429). Cooling down for {wait_seconds}s...", "magenta", attrs=['bold']))
                time.sleep(wait_seconds)
                # 這裡不 return，讓迴圈繼續跑下一次 attempt (retry)

            except (ServiceUnavailable, InternalServerError, Aborted) as e:
                tqdm.write(colored(f"  🔥 Server Error ({e.code}). Retrying in 10s...", "red"))
                time.sleep(10)

            except json.JSONDecodeError:
                tqdm.write(colored(f"  ❌ JSON Parse Error (Attempt {attempt+1})", "red"))
                if attempt < max_retries:
                    current_prompt += "\n\n[SYSTEM ERROR]: Invalid JSON."

            except Exception as e:
                tqdm.write(colored(f"  ❌ System Error: {e}", "red"))
                time.sleep(5)

        tqdm.write(colored(f"  💀 Failed after {max_retries} retries.", "red", attrs=['bold']))
        return last_result or {"error": "Max retries reached"}