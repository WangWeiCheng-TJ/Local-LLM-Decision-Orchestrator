# src/utils.py
import os
import glob
import re
import time

import google.generativeai as genai 
from termcolor import cprint


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
    