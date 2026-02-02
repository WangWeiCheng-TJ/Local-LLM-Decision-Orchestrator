import os
import glob
import chromadb
import google.generativeai as genai
from termcolor import cprint
from dotenv import load_dotenv
import json

# 引入防呆工具 (請確保 src/utils/llm_utils.py 存在)
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.utils import safe_generate_json
from src.utils import extract_text_from_pdf

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")
CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "/app/data/chroma_db")
RAW_DATA_PATH = "/app/data/raw" # 這裡放你所有的個人資料 (PDF/MD/TXT)

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel(MODEL_NAME)

def extract_text(file_path):
    """
    智慧讀取：先嘗試一般讀取，讀不到就切換 OCR。
    """
    ext = os.path.splitext(file_path)[1].lower()
    filename = os.path.basename(file_path)

    try:
        # === 處理 PDF ===
        if ext == ".pdf":
            # 使用 utils 中的 extract_text_from_pdf (基於 utils.py:12)
            text, used_ocr = extract_text_from_pdf(file_path, model_name=MODEL_NAME)
            # [修正點 1] 回傳通用的 "pdf_document"，不要在這裡定死它是 resume
            return text, "pdf_document"

        # === 處理筆記 (MD/TXT) ===
        elif ext in [".md", ".txt"]:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read(), "personal_note"

        # === [NEW] 處理 JSON (user_profile.json) ===
        elif ext == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                json_content = json.load(f)
                
                # 如果是 user_profile.json，標記為特殊類型，不要過度 summarize
                if filename == "user_profile.json":
                    text = json.dumps(json_content, indent=2, ensure_ascii=False)
                    return text, "user_profile"  # 特殊 doc_type
                else:
                    text = json.dumps(json_content, indent=2, ensure_ascii=False)
                    return text, "structured_data"

        else:
            return None, None

    except Exception as e:
        cprint(f"❌ 讀取檔案失敗 {file_path}: {e}", "red")
        return None, None

def indexer_agent_process(filename, text, doc_type):
    # 如果是 user_profile，直接跳過 LLM，用原始 metadata
    if doc_type == "user_profile":
        return {
            "summary": "User Profile (Pre-computed cheat sheet)",
            "domain": "Career Profile",
            "tags": ["#UserProfile", "#Skills", "#Education"],
            "is_resume": False
        }
    else:
        prompt = f"""
        You are my Personal Data Archivist.
        I am ingesting a document into my personal knowledge base.
        
        Filename: {filename}
        Type: {doc_type}
        Content Snippet: {text}
        
        ### TASK
        1. Identify the **Topic/Domain** (e.g., "Resume V1", "Project Alpha Notes", "Research Idea").
        2. Extract **Keywords/Skills** mentioned.
        3. Summarize the content in one sentence.
        
        ### OUTPUT JSON
        {{
            "summary": "Brief summary of this file.",
            "domain": "Computer Vision / System Design / Career Profile",
            "tags": ["#Tag1", "#Tag2"],
            "is_resume": true/false
        }}
        """
        
        default_res = {
            "summary": "Processing Failed",
            "domain": "Unknown",
            "tags": [],
            "is_resume": False
        }

    return safe_generate_json(model, prompt, retries=3, default_output=default_res)

def ingest_personal_data():
    cprint(f"🚀 [Level 0] 開始建置個人知識庫 (Ingesting Personal Data)...", "cyan", attrs=['bold'])
    
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    # 我們把 Collection 名字改得更通用一點，叫 "personal_knowledge"
    collection = client.get_or_create_collection(name="personal_knowledge")
    
    files = glob.glob(os.path.join(RAW_DATA_PATH, "*"))
    
    count = 0
    for file_path in files:
        filename = os.path.basename(file_path)
        
        # 1. 讀取
        content, doc_type = extract_text(file_path)
        if not content: continue
        
        cprint(f"\n📄 分析檔案: {filename} ({doc_type})", "white")

        # 2. AI 理解 & 標記
        cprint("   🤖 Indexer Agent Analyzing...", "blue")
        metadata = indexer_agent_process(filename, content, doc_type)

        cprint(f"   🏷️ Domain: {metadata.get('domain')}", "green")
        cprint(f"   📝 Summary: {metadata.get('summary')}", "green")

        # 3. 格式化 Metadata
        storage_meta = {
            "filename": filename,
            "doc_type": doc_type,
            "domain": metadata.get("domain", "Unknown"),
            "tags": ", ".join(metadata.get("tags", [])),
            "is_resume": str(metadata.get("is_resume", False)), # Chroma 不存 bool，轉字串
            "summary": metadata.get("summary", "")
        }

        # 4. 存入 (整份存入，不切塊，保持完整語意)
        try:
            collection.upsert(
                documents=[content],
                metadatas=[storage_meta],
                ids=[filename]
            )
            cprint("   ✅ Saved to Knowledge Base", "magenta")
            count += 1
        except Exception as e:
            cprint(f"❌ DB Error: {e}", "red")

    cprint(f"\n🎉 建置完成！你的數位分身現在擁有 {count} 份記憶。", "cyan", attrs=['bold'])

if __name__ == "__main__":
    ingest_personal_data()