import os
import json
import chromadb
from termcolor import cprint
import sys
from dotenv import load_dotenv

# 引用工具
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.utils import safe_generate_json

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")

class ProfileGeneratorAgent:
    def __init__(self, model, db_path):
        self.model = model
        self.db_path = db_path

    def _fetch_context_from_db(self):
        """
        從個人資料庫撈取資料，並自動過濾掉學術論文 (Noise)。
        """
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"DB not found at {self.db_path}")

        client = chromadb.PersistentClient(path=self.db_path)
        collection = client.get_collection("personal_knowledge")

        # 1. 撈取資料 (加大 limit，因為我們可能會濾掉很多論文)
        # get() 不帶 where 條件預設是撈所有的 ID，但為了效能我們先抓前 30 筆
        results = collection.get(limit=30)
        
        if not results['documents']:
            cprint("❌ Database is empty! Please run ingest_personal_data.py first.", "red")
            return ""
        
        context = ""
        skipped_count = 0
        
        # 定義要跳過的關鍵字 (小寫)
        # 如果 ingest 的時候標記了 "Research Paper" 或 "ArXiv"，這裡就會擋掉
        SKIP_KEYWORDS = ["paper", "arxiv", "publication", "journal", "conference", "proceeding", "thesis"]
        
        cprint(f"🔍 Scanning {len(results['documents'])} documents from DB...", "cyan")

        for i, doc in enumerate(results['documents']):
            # 取得 metadata
            meta = results['metadatas'][i] if results['metadatas'] else {}
            fname = results['ids'][i]
            
            # 取出判斷用的欄位
            domain = meta.get('domain', '').lower()
            tags = meta.get('tags', '').lower()
            doc_type = meta.get('doc_type', 'unknown')
            
            # === [過濾邏輯] ===
            # 如果 Domain 或 Tags 包含 "paper", "arxiv" 等字眼，且不是明確的 Resume，就跳過
            is_paper = any(k in domain for k in SKIP_KEYWORDS) or \
                       any(k in tags for k in SKIP_KEYWORDS)
            
            # 特別保留：如果檔名或標籤明確說是 Resume/CV，就算它被標成 paper 也要留著
            is_resume_flag = meta.get('is_resume', 'False').lower() == 'true'
            
            if is_paper and not is_resume_flag:
                cprint(f"   🚫 Skipping Paper: {fname} (Domain: {meta.get('domain')})", "dark_grey")
                skipped_count += 1
                continue
                
            # === [加入 Context] ===
            cprint(f"   📥 Loading: {fname} (Domain: {meta.get('domain')})", "white")
            # 每個檔案擷取前 5000 字，避免 Context Window 爆掉
            context += f"\n=== FILE: {fname} (Type: {doc_type}, Domain: {meta.get('domain')}) ===\n{doc[:5000]}\n"
        
        if skipped_count > 0:
            cprint(f"   (Filtered out {skipped_count} academic/paper documents to reduce noise)", "yellow")
            
        return context

    def generate_profile(self) -> str:
        cprint("🧠 Profile Generator extracting insights from Personal DB...", "cyan")
        
        context = self._fetch_context_from_db()
        if not context:
            return "Error: No relevant personal data found (Papers were filtered out)."

        # === Analysis Prompt ===
        prompt = f"""
        You are a Career Agent analyzing the user's **Personal Knowledge Base**.
        
        ### SOURCE DATA (Filtered Personal Notes & Records):
        {context}

        ### MISSION:
        Organize this information into a cohesive **Job Triage Profile**.
        Infer the user's seniority, skills, and preferences based on their actual work records.

        ### INFERENCE TASKS:
        1. **Education**: Does the user mention a PhD or Lab work?
        2. **Current Context**: Where are they based?
        3. **True Level**: Based on the *technical depth* of these notes, are they Junior, Senior, or Expert?
        4. **Tech Stack**: 
           - **Primary**: What tools appear in active, positive contexts?
           - **Anti-Stack**: What tools are absent or mentioned negatively? (Infer: If only AI/Python is present, assume Web/Legacy stacks are unwanted).
        5. **Role Fit**: What job titles match the work described here?

        ### OUTPUT JSON SCHEMA:
        {{
            "education_level": "...",
            "current_location": "...", 
            "seniority_level": "...",
            "primary_stack": ["..."],
            "anti_stack": ["..."], 
            "target_roles": ["..."],
            "avoid_roles": ["..."],
            "relocation_inference": "..."
        }}
        """

        data = safe_generate_json(self.model, prompt)
        
        # 轉成 Markdown (Triage 用)
        markdown_output = f"""# 🛡️ Personal Triage Profile (Auto-Generated)
> **Source**: Personal Database (Papers Filtered)
> **Date**: (Auto)

## 1. 🎓 Professional Core
- **Education**: {data.get('education_level', 'Unknown')}
- **Inferred Level**: {data.get('seniority_level', 'Senior')}
- **Target Roles**: {", ".join(data.get('target_roles', []))}
- **Roles to Avoid**: {", ".join(data.get('avoid_roles', []))}

## 2. 🌍 Location & Relocation
- **Current Base**: {data.get('current_location', 'Unknown')}
- **Inferred Preference**: {data.get('relocation_inference', 'Unknown')}

## 3. 🛠️ Tech Stack Strategy
- **🚀 Primary Stack (Keep)**: 
  {", ".join(data.get('primary_stack', []))}

- **🛑 Anti-Stack (Reject)**: 
  *Inferred from lack of presence or context in DB.*
  {", ".join(data.get('anti_stack', []))}

## 4. 🧠 Agent Observations
- Analyzed {len(context) // 100} units of personal context (excluding papers).
- Inferred focus: {", ".join(data.get('primary_stack', [])[:3])}.
"""
        return markdown_output

if __name__ == "__main__":
    from dotenv import load_dotenv
    import google.generativeai as genai
    
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    chroma_path = os.getenv("CHROMA_DB_PATH", "/app/data/chroma_db")
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL_NAME)
    
    agent = ProfileGeneratorAgent(model, chroma_path)
    print(agent.generate_profile())