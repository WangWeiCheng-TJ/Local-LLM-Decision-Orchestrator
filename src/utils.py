# src/utils.py
import os
import glob
import re
import time
import json

import google.generativeai as genai 
from termcolor import cprint
from pypdf import PdfReader

import chromadb
from termcolor import cprint

CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "/app/data/chroma_db")

def extract_text_from_pdf(filepath, model_name="gemini-1.5-flash"):
    """
    [新增] 通用讀取工具：優先嘗試 pypdf，失敗或字數太少則自動轉 OCR
    這樣 ingest 和 scout 都可以直接 import 這個函式。
    """
    text = ""
    used_ocr = False
    filename = os.path.basename(filepath)

    # 1. 嘗試 pypdf
    try:
        reader = PdfReader(filepath)
        for page in reader.pages:
            content = page.extract_text()
            if content: text += content + "\n"
    except Exception:
        pass 

    # 2. OCR Fallback (直接呼叫同檔案內的 gemini_ocr)
    if len(text.strip()) < 50:
        cprint(f"   👁️ [OCR Triggered] Content too short: {filename}", "cyan")
        # 假設 gemini_ocr 就在這個檔案下面定義好了
        text = gemini_ocr(filepath, model_name=model_name)
        used_ocr = True
    
    return text, used_ocr

def identify_application_packet(folder_path):
    """
    掃描指定資料夾，根據檔名關鍵字識別 JD, CV, Cover Letter 和 Outcome。
    
    Args:
        folder_path (str): 目標資料夾路徑
        
    Returns:
        dict: 包含 'jd', 'resume', 'cl', 'outcome' 路徑的字典
    """
    packet = {
        "jd": None,
        "resume": None,
        "cl": None,
        "outcome": None,
        "folder": folder_path
    }
    
    if not os.path.exists(folder_path):
        return packet

    # 取得所有檔案 (不包含子目錄)
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    
    for f in files:
        fname = f.lower()
        full_path = os.path.join(folder_path, f)
        
        # 1. 識別 JD (優先權：只要有 jd, job 就中)
        if not packet["jd"] and any(x in fname for x in ["jd", "job", "description", "vacancy", "role"]):
            packet["jd"] = full_path
            
        # 2. 識別 Resume/CV
        elif not packet["resume"] and any(x in fname for x in ["resume", "cv", "curriculum"]):
            packet["resume"] = full_path
            
        # 3. 識別 Cover Letter
        elif not packet["cl"] and any(x in fname for x in ["cl", "cover", "letter"]):
            packet["cl"] = full_path
            
        # 4. 識別結果 (Outcome/Status)
        elif not packet["outcome"] and any(x in fname for x in ["outcome", "reject", "decision", "offer", "status", "result"]):
            packet["outcome"] = full_path

    return packet

def list_history_folders(base_path):
    """ 列出該路徑下所有的第一層子資料夾 """
    return [f.path for f in os.scandir(base_path) if f.is_dir()]

# --- [新增] 通用 OCR 工具 ---
def gemini_ocr(filepath, model_name="gemini-1.5-flash"):
    """
    通用 OCR 模組：
    1. 上傳檔案
    2. 等待處理 (Polling)
    3. 呼叫 Vision API 轉文字
    
    預設使用 Flash 模型 (速度快、便宜、Rate Limit 高)
    """
    cprint(f"   👁️ [Utils] 啟動 Cloud OCR: {os.path.basename(filepath)}", "magenta")
    
    try:
        # 1. 上傳
        sample_file = genai.upload_file(path=filepath, display_name="OCR_Target")
        
        # 2. 等待處理
        while sample_file.state.name == "PROCESSING":
            time.sleep(1)
            sample_file = genai.get_file(sample_file.name)
        
        # 3. 生成
        model = genai.GenerativeModel(model_name)
        response = model.generate_content([sample_file, "Extract all text from this document accurately."])
        
        return response.text
    except Exception as e:
        cprint(f"   ❌ OCR 失敗: {e}", "red")
        return None
    
def clean_json_text(text):
    """
    專門用來清洗 LLM 吐回來的 JSON 字串。
    去除 Markdown 標記 (```json ... ```) 和多餘空白。
    """
    # 1. 移除 ```json 和 ``` 標記
    text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    
    # 2. 有時候 LLM 會在 JSON 前後加廢話，嘗試抓出 { ... } 的部分
    # 簡單的正則表達式尋找最外層的 {}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
        
    return text.strip()

def safe_generate_json(model, prompt, retries=3, delay=20, default_output=None):
    """
    這就是你的「防呆防護罩」。
    
    Args:
        model: Gemini model 物件
        prompt: 提示詞
        retries: 重試次數 (預設 3 次)
        delay: 每次重試中間休息幾秒 (預設 2 秒)
        default_output: 如果全失敗，要回傳什麼預設值 (避免 Crash)
    
    Returns:
        dict: 解析好的 JSON 資料
    """
    for attempt in range(retries):
        try:
            # 1. 發送請求
            response = model.generate_content(prompt)
            
            # 2. 清洗文字
            cleaned_text = clean_json_text(response.text)
            
            # 3. 嘗試解析 JSON
            data = json.loads(cleaned_text)
            return data

        except json.JSONDecodeError as e:
            cprint(f"⚠️ [Attempt {attempt+1}/{retries}] JSON 解析失敗: {e}", "yellow")
            # 這裡可以加一段邏輯：如果解析失敗，再次丟給 LLM 叫它修正格式 (Auto-Repair)
            # 但為了簡單，我們先重試就好
            
        except exceptions.ResourceExhausted:
            cprint(f"⚠️ [Attempt {attempt+1}/{retries}] Rate Limit (429). Cooling down...", "yellow")
            time.sleep(delay * 2 * (attempt + 1)) # 指數退避，越等越久
            continue

        except Exception as e:
            cprint(f"⚠️ [Attempt {attempt+1}/{retries}] API Error: {e}", "yellow")
            
        # 失敗後休息一下再試
        time.sleep(delay)

    # 如果重試次數用完還是失敗
    cprint(f"❌ API Call Failed after {retries} attempts.", "red")
    return default_output if default_output is not None else {}


    def fetch_relevant_history_resumes(jd_text, n_results=3):
        """
        根據目前的 JD，去 History DB 找出最相關的 N 份「過去履歷」。
        回傳：一個包含結構化履歷內容的 List。
        """
        try:
            client = chromadb.PersistentClient(path=CHROMA_PATH)
            # 注意：我們之前把 Resume 存進了 past_applications_jds，並標記 doc_type="RESUME"
            collection = client.get_collection("past_applications_jds")
            
            # 1. 語意搜尋：找跟這個 JD 最像的 Resume
            results = collection.query(
                query_texts=[jd_text],
                n_results=n_results,
                where={"doc_type": "RESUME"} # 只找履歷，不找過去的 JD 或 Cover Letter
            )
            
            retrieved_resumes = []
            
            for i, meta in enumerate(results['metadatas'][0]):
                # 取得原始檔名作為 ID
                source_name = meta.get('filename', f"Resume_{i}")
                folder = meta.get('folder', 'Unknown')
                
                # 我們在 ingest 時把結構化資料存進了 'analysis_json' 這個 metadata 欄位
                json_str = meta.get('analysis_json', '{}')
                
                try:
                    struct_data = json.loads(json_str)
                    retrieved_resumes.append({
                        "source_id": f"{folder}/{source_name}", # 標記來源，方便 Council 指路
                        "content": struct_data
                    })
                except:
                    continue

            return retrieved_resumes

        except Exception as e:
            cprint(f"⚠️ History Retrieval Error: {e}", "red")
            return []