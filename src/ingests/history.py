import os
import glob
import time
import json
import chromadb
import google.generativeai as genai
from termcolor import colored, cprint
from dotenv import load_dotenv
from tqdm import tqdm
import sys

# === 引入工具 ===
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.utils import safe_generate_json, extract_text_from_pdf

load_dotenv()
CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "/app/data/chroma_db")
API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")

# 路徑設定
PATH_ONGOING = "/app/data/history/ongoing"
PATH_REJECTED = "/app/data/history/rejected"
FORCE_UPDATE = os.getenv("FORCE_UPDATE", "False").lower() == "true"

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel(MODEL_NAME)

# ==========================================
# 🧠 1. Parsers (針對不同文件類型的解析器)
# ==========================================

def parse_resume_to_structured_data(text):
    """將履歷轉為結構化 JSON"""
    prompt = f"""
    You are a Resume Parser. Extract structured data from this resume text.
    
    ### RESUME TEXT:
    {text}
    
    ### TARGET SCHEMA (JSON):
    {{
        "summary": "Professional summary",
        "education": [ {{ "degree": "...", "school": "...", "year": "..." }} ],
        "work_experience": [ {{ "title": "...", "company": "...", "duration": "...", "key_responsibilities": "..." }} ],
        "technical_skills": {{ "languages": [], "frameworks": [], "tools": [] }},
        "PUBLICATIONS": [ {{ "name": "...", "publisher": "..." , "year": "..."}},
        "soft_skills": {{ "Leadership": [], "Innovation": [], "Presentations": [] }},
         ]
    }}
    """
    return safe_generate_json(model, prompt)

def indexer_agent_jd(text):
    """分析 JD (Job Description)"""
    prompt = f"""
    You are analyzing a PAST JOB APPLICATION (JD).
    Snippet: {text}
    
    Extract JSON:
    {{
        "role": "Job Title",
        "company": "Company Name",
        "experience_level": "Senior/Junior/...",
        "tech_stack": ["Skill1", "Skill2"],
        "summary": "One liner summary of the job",
        "tags": ["#Tag1"]
    }}
    """
    default = {"role": "Unknown", "company": "Unknown", "experience_level": "Unknown", "tech_stack": [], "summary": "", "tags": []}
    return safe_generate_json(model, prompt, default_output=default)

def parser_cover_letter(text):
    """分析 Cover Letter"""
    prompt = f"""
    Analyze this Cover Letter.
    Snippet: {text}
    
    Extract JSON:
    {{
        "target_role": "Role applied for",
        "target_company": "Company applied to",
        "key_selling_points": ["Point 1", "Point 2"],
        "connection": "How to apply skills to this role"
    }}
    """
    default = {"target_role": "Unknown", "target_company": "Unknown", "key_selling_points": [], "connection": "Unknown"}
    return safe_generate_json(model, prompt, default_output=default)

# ==========================================
# 🕵️ 2. Classifier (分類器)
# ==========================================

def identify_doc_type(filename, text):
    """
    判斷文件類型：JD, RESUME, COVER_LETTER
    優先看檔名，如果檔名看不出來，看內容前 2000 字
    """
    fname = filename.lower()
    
    # 1. 快速檔名規則 (Heuristics)
    if "resume" in fname or "cv" in fname:
        return "RESUME"
    if "cover" in fname and "letter" in fname:
        return "COVER_LETTER"
    if "jd" in fname or "job" in fname or "description" in fname:
        return "JD"
        
    # 2. 如果檔名模糊 (例如 "Google_2023.pdf")，用 LLM 判斷
    prompt = f"""
    Classify this document based on the snippet.
    Filename: {filename}
    Snippet: {text[:1000]}
    
    Options: ["RESUME", "COVER_LETTER", "JD", "OTHER"]
    Return JSON: {{ "doc_type": "..." }}
    """
    res = safe_generate_json(model, prompt, default_output={"doc_type": "JD"}) # 預設當作 JD
    return res.get("doc_type", "JD")

# ==========================================
# 🚀 3. Processor (主流程)
# ==========================================

def extract_text_smart(filepath):
    return extract_text_from_pdf(filepath, model_name=MODEL_NAME)

def process_folder(base_path, status_label, collection):
    search_path = os.path.join(base_path, "**", "*.pdf")
    files = glob.glob(search_path, recursive=True)
    
    if not files: return 0

    count = 0
    skipped_count = 0
    
    pbar = tqdm(files, desc=f"Processing {status_label}", unit="file")

    for filepath in pbar:
        filename = os.path.basename(filepath)
        folder_name = os.path.basename(os.path.dirname(filepath))
        pbar.set_postfix(file=filename[:15])

        # 1. 計算 ID
        safe_status = status_label.replace("/", "_").replace(" ", "_")
        doc_id = f"history_{safe_status}_{folder_name}_{filename}"
        
        # 2. Check Existing
        if not FORCE_UPDATE:
            existing = collection.get(ids=[doc_id])
            if existing and existing['ids']:
                skipped_count += 1
                continue 

        # 3. Extract Text
        text, used_ocr = extract_text_smart(filepath)
        if not text or len(text) < 50:
            tqdm.write(colored(f"   ⚠️ [Skip] Empty content: {filename}", "yellow"))
            continue

        # 4. Classify Document
        pbar.set_description(f"🔍 Classifying: {filename[:10]}...")
        doc_type = identify_doc_type(filename, text)
        
        # 5. Route & Analyze
        pbar.set_description(f"🤖 Analyzing [{doc_type}]: {filename[:10]}...")
        
        analysis_result = {}
        role_tag = "Unknown"
        
        if doc_type == "RESUME":
            analysis_result = parse_resume_to_structured_data(text)
            role_tag = "Candidate" # Resume 不一定有特定 Role
            
        elif doc_type == "COVER_LETTER":
            analysis_result = parser_cover_letter(text)
            role_tag = analysis_result.get("target_role", "Unknown")
            
        else: # Default to JD
            analysis_result = indexer_agent_jd(text)
            role_tag = analysis_result.get("role", "Unknown")

        # 6. Prepare Metadata
        # 注意：ChromaDB metadata 只能存 string/int/float/bool，不能存 dict
        # 所以要把結構化資料 json.dumps 轉成字串
        
        storage_meta = {
            "source": "history",
            "folder": folder_name,
            "filename": filename,
            "status": status_label,
            "doc_type": doc_type, # 關鍵欄位！
            "role": role_tag[:50], # 避免太長
            "summary": str(analysis_result.get("summary", ""))[:200],
            "analysis_json": json.dumps(analysis_result, ensure_ascii=False) # <--- 最精華的結構化資料存在這
        }

        # 7. Upsert
        collection.upsert(
            documents=[text],
            metadatas=[storage_meta],
            ids=[doc_id]
        )
        
        # Log Result
        type_color = "cyan" if doc_type == "JD" else "magenta" if doc_type == "RESUME" else "yellow"
        type_icon = "📄" if doc_type == "JD" else "🎓" if doc_type == "RESUME" else "✉️"
        
        msg = colored(f"   ✅ {type_icon} [{doc_type}] Indexed: {folder_name}/{filename[:20]}", "green")
        tqdm.write(msg)
        
        count += 1
        pbar.set_description(f"Processing {status_label}")
        if used_ocr: time.sleep(1)

    if skipped_count > 0:
        tqdm.write(colored(f"   (Skipped {skipped_count} existing files)", "light_grey"))
        
    return count

def ingest_history_jds():
    cprint("\n📜 [Level 0] Building History Index (Smart Mode)...", "cyan", attrs=['bold'])
    
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    # 我們可以繼續用同一個 collection，靠 metadata['doc_type'] 區分即可
    collection = client.get_or_create_collection(name="past_applications_jds")
    
    total_new = 0
    
    if os.path.exists(PATH_ONGOING):
        total_new += process_folder(PATH_ONGOING, "Ongoing", collection)
    
    print("-" * 40)
    
    if os.path.exists(PATH_REJECTED):
        total_new += process_folder(PATH_REJECTED, "Rejected", collection)

    cprint(f"\n✅ All Done! Added {total_new} new records.", "magenta", attrs=['bold'])

if __name__ == "__main__":
    ingest_history_jds()