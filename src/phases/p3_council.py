import os
import glob
import json
import sys
from dotenv import load_dotenv
from src.ingests.history import FORCE_UPDATE
from termcolor import colored, cprint
from tqdm import tqdm
import google.generativeai as genai

# === 引用工具 ===
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.agents.council import CouncilAgent
from src.utils import fetch_relevant_history_resumes # [新增引用]

# === CONFIG ===
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")

# 資料流向
PATH_PROFILE = "/app/data/personal/profile.md"           # 戰略意願 (Constraints)
PATH_PARSED_RESUME = "/app/data/personal/parsed_resume.json" # 核心實力 (Capabilities)
DIR_PENDING = "/app/data/processed/pending_council"      # 輸入：Phase 2 的結果
DIR_READY = "/app/data/processed/ready_to_apply"         # 輸出：Phase 3 的結果

# 設定為 True 會強制重跑並覆蓋已存在的結果 (適合調試 Prompt)
# FORCE_UPDATE = False 

# 確保輸出目錄存在
os.makedirs(DIR_READY, exist_ok=True)

def load_assembly_context(jd_text):
    """
    建立「組裝工廠」上下文：
    1. 戰略意願 (Profile)
    2. 零件庫 (Top 3 Relevant Resumes from History)
    """
    context_parts = []

    # 1. Constraints
    if os.path.exists(PATH_PROFILE):
        with open(PATH_PROFILE, 'r') as f:
            context_parts.append(f"### 1. STRATEGIC CONSTRAINTS:\n{f.read()}")

    # 2. History Components (The Lego Box)
    # 根據 JD 內容去撈最相關的履歷
    history_resumes = fetch_relevant_history_resumes(jd_text, n_results=3)
    
    if history_resumes:
        context_parts.append(f"### 2. RESUME COMPONENT LIBRARY (Top {len(history_resumes)} Matches):")
        for i, res in enumerate(history_resumes):
            # 將結構化 JSON 轉字串
            res_str = json.dumps(res['content'], indent=2)
            context_parts.append(f"--- [Option {i+1}] Source: {res['source_id']} ---\n{res_str}\n")
    else:
        context_parts.append("### 2. RESUME COMPONENT LIBRARY: (Empty - No history found)")
        
    return "\n\n".join(context_parts)

def load_full_candidate_context():
    """
    組裝完整的候選人戰力包：
    1. Profile.md (戰略意願)
    2. Parsed Resume (核心能力數據)
    """
    context_parts = []

    # 1. 戰略限制 (Constraints)
    if os.path.exists(PATH_PROFILE):
        with open(PATH_PROFILE, 'r', encoding='utf-8') as f:
            context_parts.append(f"### 1. STRATEGIC CONSTRAINTS & WISHES:\n{f.read()}")
    else:
        context_parts.append("### 1. STRATEGIC CONSTRAINTS: (File missing)")

    # 2. 結構化履歷 (Capabilities)
    if os.path.exists(PATH_PARSED_RESUME):
        with open(PATH_PARSED_RESUME, 'r', encoding='utf-8') as f:
            resume_data = json.load(f)
            # 轉成字串餵給 LLM
            resume_str = json.dumps(resume_data, indent=2)
            context_parts.append(f"### 2. CANDIDATE RESUME (STRUCTURED DATA):\n{resume_str}")
    else:
        cprint("⚠️ Warning: 'parsed_resume.json' not found. Council will fly blind.", "yellow")
        context_parts.append("### 2. CANDIDATE RESUME: (Missing data. Run ingest first.)")

    return "\n\n".join(context_parts)

def get_expert_color(expert_name):
    """🎨 給不同的專家分配顏色，增加視覺辨識度"""
    name = expert_name.lower()
    if "hr" in name or "gatekeeper" in name or "recruiter" in name:
        return "light_blue"      # 藍色：HR
    elif "tech" in name or "architect" in name or "engineer" in name:
        return "light_magenta"   # 紫色：技術
    elif "strategist" in name:
        return "light_green"     # 綠色：戰略
    elif "visa" in name:
        return "light_red"       # 紅色：簽證
    elif "academic" in name:
        return "cyan"            # 青色：學術
    elif "startup" in name:
        return "yellow"          # 黃色：新創
    elif "leadership" in name or "scout" in name:
        return "light_yellow"    # 亮黃：領導力
    else:
        return "white"

def run_council():
    cprint("\n🏛️  [Phase 3] EXPERT COUNCIL (Modular Diagnostics)", "cyan", attrs=['bold', 'reverse'])
    
    if not API_KEY:
        cprint("❌ API Key missing. Check .env", "red")
        return

    # 1. 初始化
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    
    # 載入所有背景知識 (Profile + Resume)
    full_context = load_full_candidate_context()
    cprint(f"📜 Context Loaded ({len(full_context)} chars).", "cyan")

    agent = CouncilAgent(model)
    
    # 2. 掃描待處理檔案
    files = glob.glob(os.path.join(DIR_PENDING, "*.json"))
    
    if not files:
        cprint(f"😴 No pending dossiers in {DIR_PENDING}. Run Phase 2 first.", "yellow")
        return

    cprint(f"📂 Evaluating {len(files)} dossiers...", "white")

    # 3. 開始迴圈
    pbar = tqdm(files, desc="🧠 Deliberating", unit="job")

    for filepath in pbar:
        filename = os.path.basename(filepath)
        target_path = os.path.join(DIR_READY, filename)

        # === Skip 機制 (非破壞性) ===
        if os.path.exists(target_path) and not FORCE_UPDATE:
            continue

        pbar.set_postfix(file=filename[:15])

        with open(filepath, 'r', encoding='utf-8') as f:
            dossier = json.load(f)
            
        role = dossier.get('basic_info', {}).get('role', 'Unknown Role')
        company = dossier.get('basic_info', {}).get('company', 'Unknown Company')
        jd_text = dossier.get('raw_content', '')

        # [關鍵修改] 針對這份 JD 去撈特定的歷史履歷
        dynamic_context = load_assembly_context(jd_text)

        # === 核心：Council 辯論 (Agent Call) ===
        try:
            strategy = agent.deliberate(dossier, full_context)
        except Exception as e:
            tqdm.write(colored(f"⚠️ Council Error on {filename}: {e}", "red"))
            continue

        # 將策略結果寫入 Dossier
        dossier['council_strategy'] = strategy
        
        # === 4. 視覺化儀表板 (Modular Dashboard) ===
        eval_data = strategy.get("evaluation_data", {})
        verdict = eval_data.get("verdict", "Stretch")
        
        # Header (根據 Verdict 變色)
        v_color = "green" if verdict == "High Potential" else "yellow" if verdict == "Stretch" else "red"
        tqdm.write(colored(f"\n🎯 {company} - {role} ", "white", attrs=['bold']) + colored(f"[{verdict}]", v_color))

        # A. Feature Extraction (Skills)
        matched = eval_data.get("matched_skills", [])
        missing = eval_data.get("missing_critical_skills", [])
        
        if matched:
            # 只顯示前 4 個，避免洗版
            tqdm.write(colored(f"   ✅ Matched: {', '.join(matched[:4])}...", "green"))
        if missing:
            tqdm.write(colored(f"   ⛔ Missing: {', '.join(missing)}", "red", attrs=['bold']))

        # B. Section Diagnostics (The Matrix)
        diagnostics = eval_data.get("section_diagnostics", {})
        
        # 為了版面整潔，如果全部都是 Keep，就顯示一行 Summary 就好
        needs_work = any(d.get("action") != "Keep" for d in diagnostics.values())
        
        if needs_work:
            tqdm.write(colored("   🔧 Blueprint:", "white", attrs=['bold']))
            sections = ["summary", "work_experience", "projects", "education", "skills", "publications"]
            
            for sec in sections:
                data = diagnostics.get(sec, {"action": "Keep", "reason": ""})
                action = data.get("action", "Keep")
                reason = data.get("reason", "")
                
                # 顯示邏輯
                if action == "Overhaul": 
                    a_color = "light_red"
                    icon = "🔨"
                elif action == "Tweak": 
                    a_color = "yellow"
                    icon = "🔧"
                else: 
                    continue # Keep 的就不顯示了，保持專注

                # 格式化輸出
                label = sec.replace("_", " ").title()
                tqdm.write(colored(f"      {icon} {label:<10}: {action}", a_color) + colored(f" ({reason[:50]}...)", "dark_grey"))
        else:
            tqdm.write(colored("   ✨ Resume Structure: Perfect Match (Keep As Is)", "green"))

        tqdm.write(colored("-" * 60, "dark_grey"))

        # === 5. 存檔 ===
        # 存入 Ready 資料夾 (Overwrite)
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(dossier, f, indent=2, ensure_ascii=False)
        
        # 不刪除原始檔案 (Non-Destructive)
        # os.remove(filepath)

    cprint("\n🎉 Phase 3 Complete. Strategies defined.", "magenta", attrs=['bold'])
    cprint(f"   🚀 Ready to Apply: {len(glob.glob(os.path.join(DIR_READY, '*.json')))} jobs", "green")

if __name__ == "__main__":
    run_council()