import json
import time
import re
import ast
from termcolor import colored
from tqdm import tqdm

def extract_json_from_text(text):
    """
    🧹 強力清潔劑：抓出 JSON 字串
    """
    # 1. 抓 Markdown ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match: return match.group(1)
    
    match_list = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if match_list: return match_list.group(1)

    # 2. 抓最外層括號 (針對 Gemma 沒寫 markdown 的情況)
    text = text.strip()
    start_brace = text.find('{')
    start_bracket = text.find('[')
    
    start = -1
    end = -1
    
    # 判斷是 Dict 還是 List 開頭
    if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
        start = start_brace
        end = text.rfind('}')
    elif start_bracket != -1:
        start = start_bracket
        end = text.rfind(']')
        
    if start != -1 and end != -1 and end > start:
        return text[start:end+1]
        
    return text

def aggressive_fix_json(bad_json_str):
    """
    🩹 暴力修復：把 Python Dict 字串硬轉成 JSON
    """
    try:
        # 處理 Python None/True/False
        py_str = bad_json_str.replace("null", "None").replace("true", "True").replace("false", "False")
        return ast.literal_eval(py_str)
    except:
        pass

    try:
        # 簡單字串替換
        fixed = bad_json_str.replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null")
        return json.loads(fixed)
    except:
        return None

def _coerce_skill_list(items):
    """
    把 [str, str, ...] 或混雜的 list 轉成合法技能物件。
    字串會變成 {"topic": s, "analysis": {"quote_from_jd": s}}，通過 validate_council_skill。
    """
    out = []
    for x in items:
        if isinstance(x, dict):
            out.append(x)
        else:
            s = (str(x) or "").strip()
            if len(s) >= 3:  # validator 要求 quote 至少 3 字元
                out.append({"topic": s, "analysis": {"quote_from_jd": s}})
    return out


def normalize_structure(data):
    """
    🔧 結構正規化：幫 Gemma 整理房間
    不管它回傳什麼，最後都整理成標準格式。
    """
    # 1. 如果是 List，根據內容判斷是 Skill 還是 Gap
    if isinstance(data, list):
        if not data: return {"required_skills": [], "gap_analysis": []} # 空清單
        
        # 偷看第一筆資料長怎樣
        first_item = data[0]
        if isinstance(first_item, dict) and ("effort_assessment" in first_item or "fixing_strategy" in first_item):
            return {"gap_analysis": data}
        else:
            return {"required_skills": _coerce_skill_list(data)}

    # 2. 如果是 Dict，檢查 Key 是否正確
    if isinstance(data, dict):
        # 修正 Phase 1 Skill 常見錯誤 Key
        wrong_skill_keys = ["skills", "requirements", "extraction", "output", "result", "items"]
        for key in wrong_skill_keys:
            if key in data and isinstance(data[key], list):
                data["required_skills"] = data.pop(key) # 改名
                
        # 修正 Phase 3 Gap 常見錯誤 Key
        wrong_gap_keys = ["gaps", "analysis", "assessment", "gap_report"]
        for key in wrong_gap_keys:
            if key in data and isinstance(data[key], list):
                data["gap_analysis"] = data.pop(key)

        # 處理單一物件 (Single Object) 的情況
        # 如果它直接回傳 {"topic": "Rust", ...} 而不是 List
        if "topic" in data and "required_skills" not in data and "gap_analysis" not in data:
            if "effort_assessment" in data:
                return {"gap_analysis": [data]}
            else:
                return {"required_skills": [data]}

        # 若 required_skills 裡有字串，一併轉成合法物件
        if "required_skills" in data and isinstance(data["required_skills"], list):
            data["required_skills"] = _coerce_skill_list(data["required_skills"])

    return data

def generate_with_retry(model, prompt, validator_func, max_retries=2):
    """
    通用重試機制 (Gemma 穩定版)
    """
    current_prompt = prompt
    last_result = None
    
    system_reminder = "\n\n[SYSTEM]: Return raw JSON only. Use double quotes."

    for attempt in range(max_retries + 1):
        try:
            final_prompt = current_prompt + (system_reminder if attempt > 0 else "")
            response = model.generate_content(final_prompt)
            
            # 1. 萃取
            cleaned_text = extract_json_from_text(response.text)
            
            # 2. 解析
            try:
                result_json = json.loads(cleaned_text)
            except json.JSONDecodeError:
                result_json = aggressive_fix_json(cleaned_text)
                if result_json is None:
                    raise json.JSONDecodeError("Fix failed", cleaned_text, 0)
            
            # 3. [關鍵新增] 正規化結構 (Normalize)
            # 在驗證之前，先把格式修好
            result_json = normalize_structure(result_json)

            last_result = result_json
            
            # 4. 驗證
            is_valid, error_msg = validator_func(result_json)
            
            if is_valid:
                if attempt > 0:
                    tqdm.write(colored(f"  ✨ Auto-repaired on attempt {attempt+1}", "yellow"))
                return result_json
            
            # 失敗處理
            tqdm.write(colored(f"  ⚠️ Validation failed (Attempt {attempt+1}): {error_msg}", "light_red"))
            
            if attempt < max_retries:
                current_prompt += f"\n\n[SYSTEM ERROR]: {error_msg}. Check your JSON structure keys."
                time.sleep(20*max_retries)

        except json.JSONDecodeError:
            tqdm.write(colored(f"  ❌ JSON Parsing Error (Attempt {attempt+1})", "red"))
            if attempt < max_retries:
                current_prompt += "\n\n[SYSTEM ERROR]: Invalid JSON. Use standard JSON format."

        except Exception as e:
            tqdm.write(colored(f"  ❌ System Error: {e}", "red"))
            time.sleep(20)

    tqdm.write(colored(f"  💀 Failed after {max_retries} retries.", "red", attrs=['bold']))
    return last_result or {"error": "Max retries reached"}