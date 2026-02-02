import os
import json
import chromadb
from termcolor import cprint

# 設定 DB 路徑 (跟你的 ingestion script 保持一致)
CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "/app/data/chroma_db")
USER_PROFILE_PATH = os.getenv("PATH_TO_USER_PROFILE", "/app/data/chroma_db")


class DBConnector:
    def __init__(self):
        if not os.path.exists(CHROMA_PATH):
            cprint(f"⚠️ ChromaDB path not found: {CHROMA_PATH}", "yellow")
            self.client = None
        else:
            self.client = chromadb.PersistentClient(path=CHROMA_PATH)
        self.data_dir = USER_PROFILE_PATH

    def get_personal_knowledge_context(self):
        """
        📖 從 'personal_knowledge' 取出所有個人筆記與研究
        回傳格式：純文字字串 (給 LLM 讀的)
        """
        if not self.client: return "(DB Not Connected)"
        
        try:
            collection = self.client.get_collection("personal_knowledge")
            # 這裡我們先取出所有資料 (假設個人筆記量還沒大到爆掉 Token)
            # 如果資料量很大，這裡可以改用 collection.query(query_texts=[skill_keyword]) 做語意搜尋
            results = collection.get() 
            
            context_text = ""
            if not results['ids']:
                return "(Personal DB is empty)"

            for i, doc_id in enumerate(results['ids']):
                filename = results['metadatas'][i].get('filename', 'Unknown')
                domain = results['metadatas'][i].get('domain', 'General')
                content = results['documents'][i]
                
                context_text += f"=== SOURCE: {filename} (Domain: {domain}) ===\n"
                context_text += f"{content}\n\n"
                
            return context_text
        except Exception as e:
            return f"(Error reading Personal DB: {e})"

    def get_user_profile(self):
        # [IMPROVED] 讀取使用者 profile，支援 fallback
        # Priority:
        # 1. user_profile.json (手動，最快)
        # 2. auto_generated_user_profile.json (自動生成，次快)
        # 3. ChromaDB query (最慢，但總能運作)

        # === Priority 1: 手動 user_profile.json ===
        manual_path = os.path.join(self.data_dir, "user_profile.json")
        if os.path.exists(manual_path):
            try:
                with open(manual_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cprint("✅ Using manual user_profile.json", "green")
                    return json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as e:
                cprint(f"⚠️ Failed to load manual user_profile.json: {e}", "yellow")
        
        # === Priority 2: 自動生成的 profile ===
        auto_path = os.path.join(self.data_dir, "auto_generated_user_profile.json")
        if os.path.exists(auto_path):
            try:
                with open(auto_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cprint("✅ Using auto_generated_user_profile.json", "cyan")
                    return json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as e:
                cprint(f"⚠️ Failed to load auto profile: {e}", "yellow")
        
        # === Priority 3: ChromaDB fallback ===
        cprint("⚠️ No user_profile found, using ChromaDB query fallback...", "yellow")
        
        if not self.client:
            return "{}"
        
        try:
            collection = self.client.get_collection("personal_knowledge")
            results = collection.query(
                query_texts=["technical skills, education, work experience, preferences"],
                n_results=3
            )
            
            if not results['documents']:
                return "{}"
            
            fallback_summary = {
                "source": "chromadb_realtime_query",
                "note": "No pre-computed profile found, generated on-the-fly",
                "content": "\n\n---\n\n".join(results['documents'][0]) if results['documents'] else ""
            }
            
            cprint("⚠️ Using ChromaDB query fallback (slowest)", "red")
            return json.dumps(fallback_summary, indent=2, ensure_ascii=False)
            
        except Exception as e:
            cprint(f"❌ All fallbacks failed: {e}", "red")
            return "{}"

    def get_resume_bullets_context(self):
        """
        🎓 從 'past_applications_jds' 取出所有 'RESUME' 類型的結構化資料
        重點：我們需要解析 metadata 裡的 'analysis_json' 來拿到 bullet points
        """
        if not self.client: return "(DB Not Connected)"

        try:
            collection = self.client.get_collection("past_applications_jds")
            # 只抓 doc_type = RESUME 的資料
            results = collection.get(where={"doc_type": "RESUME"})
            
            context_text = ""
            if not results['ids']:
                return "(Resume DB is empty - No documents tagged as RESUME)"

            for i, doc_id in enumerate(results['ids']):
                filename = results['metadatas'][i].get('filename', 'Unknown')
                json_str = results['metadatas'][i].get('analysis_json', '{}')
                
                try:
                    resume_data = json.loads(json_str)
                except:
                    continue # 解析失敗就跳過
                
                context_text += f"=== RESUME VERSION: {filename} ===\n"
                
                # 提取 Summary
                if 'summary' in resume_data:
                    context_text += f"[Summary]: {resume_data['summary']}\n"
                
                # 提取 Work Experience (這就是我們要找 Bullet Points 的地方)
                work_exp = resume_data.get('work_experience', [])
                if isinstance(work_exp, list):
                    for job in work_exp:
                        title = job.get('title', 'Role')
                        company = job.get('company', 'Company')
                        bullets = job.get('key_responsibilities', '') 
                        # 有時候 parser 會把 bullets 存成 list 或 string，這裡做個防呆
                        
                        context_text += f"[Job]: {title} at {company}\n"
                        context_text += f"  - Bullets: {bullets}\n"
                
                # 提取 Projects 或 Technical Skills
                skills = resume_data.get('technical_skills', {})
                context_text += f"[Skills]: {json.dumps(skills, ensure_ascii=False)}\n\n"

            return context_text

        except Exception as e:
            return f"(Error reading Resume DB: {e})"

# 實例化全域物件
db_connector = DBConnector()