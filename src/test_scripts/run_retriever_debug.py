import os
import glob
import json
import sys
from termcolor import colored, cprint
from dotenv import load_dotenv
import google.generativeai as genai

# === 路徑設定 ===
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))) 
sys.path.append(os.path.abspath(".")) 

# 引入工具
try:
    from src.agents.character_setting.prompt_loader import PromptFactory
    from src.tools.model_gateway import SmartModelGateway
    from src.tools.db_connector import db_connector
except ImportError as e:
    cprint(f"❌ Import Error: {e}", "red"); sys.exit(1)

# === CONFIG ===
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
DIR_TARGET = "/app/data/processed/pending_council"  # 讀取還在處理中的檔案

# ==========================================
# 🛡️ 本地驗證器 (為了避免依賴 retry.py)
# ==========================================
def validate_gap_effort(data):
    if not isinstance(data, dict): return False, "Not a dict"
    gaps = data.get("gap_analysis", [])
    if not gaps and "gap_analysis" not in data: 
         return False, f"Missing 'gap_analysis'. Found keys: {list(data.keys())}"
    
    # 簡單檢查內容
    if gaps and isinstance(gaps, list):
        first = gaps[0]
        # 檢查是否包含 Retriever 必要的欄位
        if "evidence_in_personal_db" not in first:
             return False, "Missing 'evidence_in_personal_db' field"
        if "resume_reusability" not in first:
             return False, "Missing 'resume_reusability' field"

    return True, ""

# ==========================================
# 📊 視覺化報告
# ==========================================
def print_retrieval_report(eid, result):
    gaps = result.get("gap_analysis", [])
    if not gaps:
        cprint(f"  ❌ {eid} returned no analysis.", "red")
        return

    print(colored(f"\n🔎 Retriever Report for {eid}", "white", attrs=['bold', 'reverse']))
    
    # 表格設計
    header = f"| {'Skill Topic':<25} | {'Personal DB Evidence (Fact)':<35} | {'Resume DB (Draft Bullet)':<35} |"
    divider = "-" * len(header)
    print(divider)
    print(header)
    print(divider)

    for item in gaps:
        topic = item.get("topic", "Unknown")[:23]
        
        # 1. Personal DB 檢索結果
        db_ev = item.get("evidence_in_personal_db", {})
        status = db_ev.get("status", "N/A")
        snippet = str(db_ev.get("evidence_snippet", "None")).replace('\n', ' ')
        
        # 顏色邏輯
        if "NOT_FOUND" in status:
            snippet_raw = colored("❌ Not Found", "red")
        elif "WEAK" in status:
            snippet_raw = colored(snippet[:33]+"..", "yellow")
        else:
            snippet_raw = colored(snippet[:33]+"..", "green")

        # 2. Resume DB 檢索結果
        res_ev = item.get("resume_reusability", {})
        res_status = res_ev.get("status", "N/A")
        bullet = str(res_ev.get("closest_existing_bullet", "None")).replace('\n', ' ')
        
        if "NO_MATCH" in res_status:
            resume_raw = colored("⚠️ New Content Needed", "yellow")
        else:
            resume_raw = colored(bullet[:33]+"..", "cyan")

        print(f"| {topic:<25} | {snippet_raw:<44} | {resume_raw:<44} |")
    print(divider + "\n")

# ==========================================
# 🚀 主程式
# ==========================================
def run_retriever_debug():
    cprint("\n🕵️‍♂️  RETRIEVER DIAGNOSTIC TOOL (Powered by SmartGateway)", "cyan", attrs=['bold'])
    
    # 1. 初始化 Gateway (自動管理 Gemma/Flash 切換)
    try:
        gateway = SmartModelGateway(API_KEY)
        factory = PromptFactory(root_dir=os.path.abspath("src/agents"))
        cprint("🏭 Gateway & Factory loaded.", "green")
    except Exception as e:
        cprint(f"❌ Init Error: {e}", "red"); return

    # 2. 連接資料庫 (自動抓取 ChromaDB)
    cprint("🔌 Connecting to ChromaDB...", "white")
    personal_db_context = db_connector.get_personal_knowledge_context()
    resume_db_context = db_connector.get_resume_bullets_context()
    
    p_len = len(personal_db_context)
    r_len = len(resume_db_context)
    
    if p_len < 50: cprint("⚠️ Personal DB is suspiciously empty.", "yellow")
    if r_len < 50: cprint("⚠️ Resume DB is suspiciously empty.", "yellow")
    
    cprint(f"📚 Context Loaded: Personal ({p_len} chars), Resume ({r_len} chars)", "green")

    # 3. 讀取目標檔案 (從 pending_council)
    files = glob.glob(os.path.join(DIR_TARGET, "*.json"))
    target_file = None
    
    # 尋找一個已經跑過 Phase 3 (有 expert_council) 的檔案
    if files:
        for f_path in files:
            with open(f_path, 'r', encoding='utf-8') as f:
                try:
                    temp = json.load(f)
                    if 'expert_council' in temp and 'skill_analysis' in temp['expert_council']:
                        target_file = f_path
                        dossier = temp
                        break
                except: continue
    
    if not target_file:
        cprint(f"❌ No valid processed dossiers found in {DIR_TARGET}. Run Phase 3 first.", "red")
        return

    company = dossier.get('basic_info', {}).get('company', 'Unknown')
    cprint(f"🎯 Target Dossier: {company} (File: {os.path.basename(target_file)})", "yellow")

    # 4. 準備 Prompt Context
    skill_map = dossier.get('expert_council', {}).get('skill_analysis', {})
    
    # 優先測試 E2 (Tech Lead) 因為他的技術檢索最重要，如果沒有就抓第一個
    target_eid = "E2" if "E2" in skill_map else list(skill_map.keys())[0]
    p1_memory = skill_map[target_eid]
    
    cprint(f"🤖 Agent: {target_eid} (Analyzing {len(p1_memory.get('required_skills', []))} skills)", "magenta")
    
    context_data = {
        "job_title": dossier.get('basic_info', {}).get('role', ''),
        "company_name": company,
        "previous_phase_memory": p1_memory, 
        "personal_db_text": personal_db_context,
        "resume_db_text": resume_db_context
    }
    
    prompt = factory.create_expert_prompt(target_eid, "GAP_EFFORT", context_data)

    # 5. 執行 (交給 Gateway 自動處理)
    cprint(f"⏳ Calling Gateway (Auto-Routing)...", "dark_grey")
    
    # 這裡會自動：算Token -> 選 Flash -> 呼叫 API -> 如果 429 就等 -> 回傳 JSON
    result = gateway.generate(
        prompt=prompt, 
        validator_func=validate_gap_effort
    )

    # 6. 顯示結果
    print_retrieval_report(target_eid, result)

if __name__ == "__main__":
    run_retriever_debug()