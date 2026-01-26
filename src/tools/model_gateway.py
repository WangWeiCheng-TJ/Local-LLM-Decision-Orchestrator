import os
import json
import re
import time
import typing
import google.generativeai as genai
from tqdm import tqdm
from termcolor import colored
from dotenv import load_dotenv
import pydantic

# ==============================================================================
# Tagged Protocol Parser (The New Secret Sauce)
# ==============================================================================
load_dotenv()
TPM_SAFE_LIMIT = os.getenv("TPM_SAFE_LIMIT", 13000)

import re

def parse_gemma_tags(raw_text: str) -> dict:
    """
    [Universal Parser V4] 
    自動識別 Phase 1 (Skill), Phase 2 (Gap), Phase 3 (Advisor) 的標籤內容，
    並建構對應的巢狀結構 (Nested Objects) 以符合 Pydantic Schema。
    """
    if not raw_text or not isinstance(raw_text, str):
        return None

    # 1. 抓取所有 @@@ 區塊
    blocks = re.findall(r'@@@(.*?)@@@', raw_text, re.DOTALL)
    if not blocks:
        # Fallback: 嘗試直接抓 JSON 或其他格式 (視情況擴充)
        return None

    results = []
    detected_type = "SKILL" # 預設類型

    for block in blocks:
        def extract(key_pattern):
            # 支援多種 alias，例如 STRATEGY|PLAN
            pattern = fr'(?:{key_pattern}):\s*(.*?)(?=\n[A-Z_]+:|$)'
            match = re.search(pattern, block, re.IGNORECASE | re.DOTALL)
            if match and match.group(1) is not None:
                return match.group(1).strip()
            return ""

        # --- 2. 特徵偵測 (Feature Detection) ---
        # 根據區塊內含有的標籤來決定這是一筆什麼資料
        
        # [Phase 2 Detection] 是否包含 EFFORT 或 STRATEGY?
        if "EFFORT" in block or "STRATEGY" in block or "EVIDENCE" in block:
            detected_type = "GAP"
            
            # 建構 Phase 2 的巢狀結構 (GapAnalysisItem)
            item = {
                "topic": extract("TOPIC|SKILL"),
                "evidence_in_personal_db": {
                    "status": extract("EVIDENCE_STATUS|STATUS") or "NOT_FOUND",
                    "evidence_snippet": extract("EVIDENCE|PROOF") or "No evidence found."
                },
                "resume_reusability": {
                    "status": extract("REUSABILITY_STATUS|REUSABILITY") or "NO_MATCH",
                    "closest_existing_bullet": extract("BULLET|CLOSEST_BULLET")
                },
                "effort_assessment": {
                    "level": extract("EFFORT_LEVEL|EFFORT") or "HIGH",
                    "strategy": extract("STRATEGY|PLAN") or "Review required.",
                    "estimated_action": extract("ACTION|ESTIMATED_ACTION") or "Update resume."
                }
            }
            results.append(item)

        # [Phase 3 Detection] 是否包含 RATIONALE 或 ADVICE?
        elif "RATIONALE" in block or "ACTIONABLE_STEP" in block:
            detected_type = "ADVISOR"
            
            item = {
                "topic": extract("TOPIC|FOCUS_AREA"),
                "rationale": extract("RATIONALE|REASONING"),
                "actionable_step": extract("ACTIONABLE_STEP|ACTION|INSTRUCTION"),
                "priority": extract("PRIORITY") or "MEDIUM"
            }
            results.append(item)

        # [Phase 1 Default] 預設為 Skill Extraction
        else:
            detected_type = "SKILL"
            
            # 建構 Phase 1 的結構 (SkillItem)
            item = {
                "topic": extract("TOPIC"),
                "priority": extract("PRIORITY") or "MUST_HAVE",
                "analysis": {
                    "hidden_bar": extract("HIDDEN_BAR|HBAR|IMPLICIT_REQUIREMENT") or "None detected.",
                    "quote_from_jd": extract("QUOTE|SOURCE") or "Contextual."
                }
            }
            results.append(item)

    # 3. 根據偵測到的類型回傳正確的 Root Key
    if detected_type == "GAP":
        return {"gap_analysis": results}
    elif detected_type == "ADVISOR":
        return {"strategic_advice": results}
    else:
        return {"required_skills": results}

# ==============================================================================
# Helper Functions: JSON Extraction & Repair
# ==============================================================================

def extract_json_from_text(text: str) -> str:
    if not text: return "" # 安全防線
    match = re.search(r'```json\s*(.*?)```', text, re.DOTALL)
    if match: return match.group(1).strip()
    
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end != -1: return text[start : end + 1]
    return text.strip() # 此時 text 必為字串

def aggressive_fix_json(bad_json: str) -> dict:
    try:
        fixed = re.sub(r',\s*}', '}', bad_json)
        fixed = re.sub(r',\s*]', ']', fixed)
        return json.loads(fixed)
    except: pass
    try:
        if bad_json.count('{') > bad_json.count('}'):
            return json.loads(bad_json + '}' * (bad_json.count('{') - bad_json.count('}')))
        if bad_json.count('[') > bad_json.count(']'):
            return json.loads(bad_json + ']' * (bad_json.count('[') - bad_json.count(']')))
    except: pass
    return None

def normalize_structure(data):
    if not isinstance(data, (dict, list)): return data
    
    # 如果 parse_gemma_tags 已經回傳了正確的 Dict 結構，直接回傳
    if isinstance(data, dict) and ("required_skills" in data or "gap_analysis" in data):
        return data

    # 這裡保留你之前的修復邏輯 (略，已整合進 parse_gemma_tags)
    return data

# ==============================================================================
# Main Class: SmartModelGateway
# ==============================================================================

class SmartModelGateway:
    def __init__(self, config):
        self.config = {}
        if isinstance(config, dict):
            self.config = config
        elif isinstance(config, str):
            if os.path.isfile(config):
                tqdm.write(colored(f"  📂 Loading config: {config}", "cyan"))
                with open(config, 'r') as f: self.config = json.load(f)
            else:
                # 認可是 API Key (不印出來)
                self.config = {"api_key": config}

        if "api_key" not in self.config:
            raise ValueError("❌ Missing 'api_key' in SmartModelGateway config.")

        genai.configure(api_key=self.config["api_key"])
        
        lt_name = os.getenv("MODEL_LT_NAME", "gemini-1.5-flash")
        main_name = os.getenv("MODEL_NAME", "gemma-3-27b-it")
        tqdm.write(colored(f"  🤖 SmartModelGateway Init: LT={lt_name}, Main={main_name}", "cyan"))
        
        self.flash_model = genai.GenerativeModel(lt_name)
        self.gemma_model = genai.GenerativeModel(main_name)

    def generate(self, prompt: str, *args, **kwargs) -> dict:
        """
        [Expert Council Edition] 
        整合: 14k TPM 哨兵、Pydantic/Function 雙模驗證、Gemma/Flash 自動導流。
        """
        # 1. 彈性參數抓取
        # 支援 schema=..., schema_model=..., 或位置參數 args[0]
        schema = kwargs.get('schema') or kwargs.get('schema_model')
        if not schema and len(args) > 0:
            schema = args[0]
            
        # 支援 use_gemma=..., 或位置參數 args[1]
        use_gemma_req = kwargs.get('use_gemma', True)
        if not use_gemma_req and len(args) > 1:
            use_gemma_req = args[1]

        # 2. Token 診斷與 TPM 哨兵
        try:
            # 使用 Flash 進行精確計數 (不計入 Gemma 的 TPM 額度)
            token_count = self.flash_model.count_tokens(prompt).total_tokens
        except:
            token_count = len(prompt) // 4 

        # 設定 TPM 安全水位為 14,000 (預留 1,000 給輸出)
        # TPM_SAFE_LIMIT = 13000 
        env_limit = os.getenv("TPM_SAFE_LIMIT", "14000")
        tpm_limit = int(env_limit)
        actual_use_gemma = use_gemma_req
        
        
        # 自動分流邏輯
        if use_gemma_req and token_count > (tpm_limit - 1000):
            print("171", tpm_limit, use_gemma_req, token_count)
            input()
            actual_use_gemma = False
            tqdm.write(colored(f"  ⚠️ TPM Sentinel: Prompt size ({token_count}) approaching {tpm_limit//1000}k limit. Auto-switching to Flash.", "yellow"))
        elif token_count > 5000:
            # 即使沒破上限，若超過 5k 也給一個提示 (協助診斷是否有資料洩漏)
            tqdm.write(colored(f"  🔍 Diagnostic: Large prompt detected ({token_count} tokens).", "magenta"))

        model = self.gemma_model if actual_use_gemma else self.flash_model
        
        # 3. 雙模式驗證核心：解決 'BaseModel.__init__() takes 1 positional argument but 2 were given'
        def run_validation(validator, target_data):
            """
            適配器：自動判斷是 Pydantic Model 類別還是普通驗證函式。
            """
            # 檢查是否為 Pydantic Model 類別
            is_pydantic = isinstance(validator, type) and issubclass(validator, pydantic.BaseModel)
            
            try:
                if is_pydantic:
                    # 模式 A: Pydantic 類別使用解包傳入 (或使用 model_validate)
                    # 這能避免將整個 dict 當成第一個 positional argument 丟進 __init__
                    validator(**target_data) 
                    return True, ""
                else:
                    # 模式 B: 普通驗證函式 (如 validate_council_skill) 直接整包傳入
                    validator(target_data)
                    return True, ""
            except Exception as e:
                return False, str(e)

        # --- 4. 智能派發器 (Smart Dispatcher) ---
        def validate_dispatcher(data):
            if not schema: return True, ""
            
            # [策略 A] 優先嘗試「整包驗證」 (Root Validation)
            # 適用於：你傳入了 SkillExtractionReport, GapAnalysisReport 等完整結構
            is_root_ok, root_err = run_validation(schema, data)
            if is_root_ok:
                return True, ""

            # [策略 B] 如果整包失敗，檢查是否為「包裝結構」並嘗試「逐項驗證」 (Item Validation)
            # 適用於：你傳入了 SkillItem，但資料被包在 {"required_skills": [...]} 裡面
            if isinstance(data, dict):
                target_keys = ["required_skills", "gap_analysis", "strategic_advice"]
                
                for key in target_keys:
                    if key in data and isinstance(data[key], list):
                        # 發現包裝層，進入拆包模式
                        for idx, item in enumerate(data[key]):
                            # 這裡是用原本的 schema 去驗證列表裡的每一個 item
                            is_item_ok, item_err = run_validation(schema, item)
                            if not is_item_ok:
                                # 這裡回傳 Item 級別的錯誤，會比 Root 錯誤更精準
                                return False, f"Item {idx} in '{key}' failed: {item_err}"
                        
                        # 如果所有 Items 都通過，代表這是 Item Schema 模式，驗證成功
                        return True, ""

            # 如果既不是整包通過，也不是包裝結構問題，那就回傳最原始的 Root 錯誤
            return False, f"Validation failed: {root_err}"

            # 2. 單一物件模式 (Fallback)
            # 如果 data 不是包裝結構，或是找不到上述 keys，嘗試直接驗證
            return run_validation(schema, data)

        # 5. 配置與執行
        gen_config = genai.types.GenerationConfig(
            temperature=0.2 if actual_use_gemma else 0.1
        )

        return self._generate_with_retry_logic(
            model=model,
            prompt=prompt,
            validator_func=validate_dispatcher,
            max_retries=3,
            generation_config=gen_config
        )


    def _generate_with_retry_logic(self, model, prompt, validator_func, max_retries, generation_config=None):
        current_prompt = prompt
        last_result, last_error_msg = None, "Unknown Error"
        
        log_dir = "data"
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "debug_gemma.log")

        for attempt in range(max_retries + 1):
            try:
                response = model.generate_content(current_prompt, generation_config=generation_config)
                raw_text = response.text if response.text else "[EMPTY]"
                
                tqdm.write(colored(f"\n👀 [DEBUG] Attempt {attempt+1}:", "cyan"))
                tqdm.write(colored(raw_text[:150].replace('\n', ' ') + "...", "white", attrs=['dark'])) 

                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n\n{'='*20} ATTEMPT {attempt+1} ({time.strftime('%H:%M:%S')}) {'='*20}\n")
                    f.write(f"--- RAW RESPONSE ---\n{raw_text}\n{'='*50}\n")

                # --- 核心解析分支 ---
                if "@@@" in raw_text:
                    result_json = parse_gemma_tags(raw_text)
                    if not result_json: raise ValueError("Tag parsing failed (Blocks found but no fields)")
                else:
                    cleaned_text = extract_json_from_text(raw_text)
                    try:
                        result_json = json.loads(cleaned_text)
                    except:
                        result_json = aggressive_fix_json(cleaned_text)
                        if result_json is None: raise ValueError("JSON parse failed")

                result_json = normalize_structure(result_json)
                last_result = result_json
                
                is_valid, error_msg = validator_func(result_json)
                if is_valid:
                    if attempt > 0: tqdm.write(colored(f"  ✨ Repaired on attempt {attempt+1}", "yellow"))
                    return result_json
                
                last_error_msg = error_msg
                tqdm.write(colored(f"  ⚠️ Validation failed: {error_msg}", "light_red"))
                
                if attempt < max_retries:
                    wait_time = 20 * (attempt + 1)
                    current_prompt += f"\n\n[SYSTEM ERROR]: {error_msg}. Please fix this and follow the protocol."
                    tqdm.write(colored(f"  ⏳ Sleeping {wait_time}s...", "yellow"))
                    time.sleep(wait_time)

            except Exception as e:
                last_error_msg = str(e)
                tqdm.write(colored(f"  ❌ Error (Attempt {attempt+1}): {e}", "red"))
                if attempt < max_retries: time.sleep(20 * (attempt + 1))

        tqdm.write(colored(f"  💀 DEAD: {last_error_msg}", "red", attrs=['bold']))
        return {"error": "Max retries reached", "failure_reason": last_error_msg, "debug_dump": last_result}