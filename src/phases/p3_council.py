import os
import glob
import json
import sys
from termcolor import colored, cprint
from tqdm import tqdm
from dotenv import load_dotenv

# === 路徑設定 ===
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))) 
sys.path.append(os.path.abspath(".")) 

# === Imports ===
try:
    from src.agents.character_setting.prompt_loader import PromptFactory
    from src.tools.model_gateway import SmartModelGateway   # [NEW] 統一入口
    from src.tools.db_connector import db_connector         # [NEW] 資料庫連線
    from src.tools.tool import validate_council_skill, validate_gap_effort
    from src.agents.cache_manager import council_memory 
    from src.tools.schemas import GapAnalysisReport
except ImportError as e:
    cprint(f"❌ Error: Import failed. {e}", "red")
    sys.exit(1)

# === CONFIG ===
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")

# 資料夾路徑
DIR_PENDING = "/app/data/processed/pending_council" 
FORCE_REFRESH = False 

# 專家 ID 對照表
ROLE_NAME_TO_ID = {
    "HR Gatekeeper": "E1", "Tech Lead": "E2", "Strategist": "E3", 
    "Visa Officer": "E4", "Academic Reviewer": "E5", "Academic": "E5",
    "System Architect": "E6", "Leadership Scout": "E7", "Startup Veteran": "E8"
}

def get_expert_color(eid):
    colors = { "E1": "cyan", "E2": "magenta", "E3": "green", "E4": "red", "E5": "blue", "E6": "yellow", "E7": "white", "E8": "light_green" }
    return colors.get(eid, "white")

def get_target_experts(dossier):
    """決定這份 JD 需要哪些專家"""
    target_ids = []
    
    # 1. Check Triage Result
    referral = dossier.get('triage_result', {}).get('referral_analysis', {})
    if referral and isinstance(referral, dict):
        for eid, data in referral.items():
            if not eid.startswith("E"): continue
            note = data.get('note', '').lower()
            score = data.get('relevance', 0)
            
            if note in ['must', 'important', 'relevant'] or score >= 6:
                target_ids.append(eid)

    # 2. Check Strategy List
    strategy = dossier.get('council_strategy', {})
    active_roles = strategy.get('active_experts', [])
    if active_roles and isinstance(active_roles, list):
        for role in active_roles:
            eid = ROLE_NAME_TO_ID.get(role)
            if eid: target_ids.append(eid)

    return sorted(list(set(target_ids))) if target_ids else ["E1", "E2"]

# ==========================================
# 🟡 Sub-Function: Step 1 (Skill Extraction)
# ==========================================
def _step1_skill_extraction(dossier, target_experts, gateway, factory, user_profile_summary):
    """
    只負責執行 Skill Extraction 的邏輯，不負責讀寫檔
    """
    company = dossier.get('basic_info', {}).get('company', 'Unknown')
    raw_jd = dossier.get('raw_content', '')
    
    # 準備 Context
    context_data = {
        "job_title": dossier.get('basic_info', {}).get('role', ''),
        "company_name": company,
        "raw_jd_text": raw_jd,
        "user_profile_summary": user_profile_summary  # <--- 注入 Prompt
    }

    tqdm.write(colored(f"  🟡 [Step 1] Extracting Skills...", "yellow"))
    
    if 'expert_council' not in dossier: dossier['expert_council'] = {}
    current_results = dossier['expert_council'].get('skill_analysis', {})

    for eid in target_experts:
        try:
            # Cache Check
            cached = council_memory.get(raw_jd, eid, "SKILL")
            if cached and not FORCE_REFRESH:
                current_results[eid] = cached
                tqdm.write(colored(f"    🧠 {eid}: Cache Hit", get_expert_color(eid)))
                continue

            # Gateway Call
            prompt = factory.create_expert_prompt(eid, "SKILL", context_data)
            result = gateway.generate(prompt, validate_council_skill)
            
            # Save Logic
            council_memory.save(raw_jd, eid, "SKILL", result)
            current_results[eid] = result
            
            count = len(result.get("required_skills", []))
            tqdm.write(colored(f"    👤 {eid}: Found {count} skills", get_expert_color(eid)))

        except Exception as e:
            tqdm.write(colored(f"    ❌ {eid} Skill Error: {e}", "red"))

    dossier['expert_council']['skill_analysis'] = current_results
    return dossier

# ==========================================
# 🔵 Sub-Function: Step 2 (Gap & Effort Analysis)
# ==========================================
def _step2_gap_analysis(dossier, gateway, factory, db_context):
    """
    執行 Phase 3.5：同時進行「證據檢索 (Retriever)」與「落差分析 (Gap Analysis)」
    [與之前不同處]：加入了 Gatekeeper 過濾邏輯，擋下 MISSING 的技能以節省 Flash 額度。
    """
    raw_jd = dossier.get('raw_content', '')
    company = dossier.get('basic_info', {}).get('company', 'Unknown')
    
    skill_map = dossier.get('expert_council', {}).get('skill_analysis', {})
    
    if 'gap_analysis' not in dossier['expert_council']:
        dossier['expert_council']['gap_analysis'] = {}
    current_gaps = dossier['expert_council']['gap_analysis']

    tqdm.write(colored(f"  🔵 [Step 2] Gap & Effort Analysis (Filtered by Gatekeeper)...", "cyan"))
    
    active_experts = list(skill_map.keys())

    for eid in active_experts:
        try:
            p1_memory = skill_map[eid]
            if not p1_memory or "required_skills" not in p1_memory: continue

            # --- A. Cache Check ---
            cached = council_memory.get(raw_jd, eid, "GAP_EFFORT")
            if cached and not FORCE_REFRESH:
                current_gaps[eid] = cached
                tqdm.write(colored(f"    🧠 {eid}: Gap Cache Hit", get_expert_color(eid)))
                continue

            # === 🛑 [NEW] Gatekeeper Filter (守門員過濾) ===
            # 目的：剔除 Step 1 已經標記為 'MISSING' 的技能，不要浪費 Flash 額度去查
            raw_skills = p1_memory.get("required_skills", [])
            skills_to_analyze = []
            skipped_count = 0

            for skill in raw_skills:
                # 檢查 Step 1 的標記 (MATCH / POTENTIAL / MISSING)
                # 如果是 MISSING，直接跳過；如果是 MATCH 或 POTENTIAL (或沒標記)，則保留
                if skill.get("quick_check") == "MISSING":
                    skipped_count += 1
                    continue
                skills_to_analyze.append(skill)
            
            # 優化：如果過濾後發現沒東西需要查 (例如全部都缺)，就直接跳過 API Call
            if not skills_to_analyze:
                tqdm.write(colored(f"    ⏩ {eid}: All {skipped_count} skills are MISSING. Skipping Flash call.", "blue"))
                # 這裡我們可以選擇塞一個空的結果，或者甚麼都不做
                # current_gaps[eid] = {"gap_analysis": [], "note": "All filtered by Gatekeeper"} 
                continue

            if skipped_count > 0:
                tqdm.write(colored(f"    🛡️ {eid}: Filtered {skipped_count} missing skills. Analyzing {len(skills_to_analyze)} items...", "blue"))

            # 建立過濾後的 Memory 物件
            p1_memory_filtered = {"required_skills": skills_to_analyze}

            # --- B. Context Injection ---
            context_data = {
                "job_title": dossier.get('basic_info', {}).get('role', ''),
                "company_name": company,
                "previous_phase_memory": p1_memory_filtered, # <--- 傳入過濾後的清單
                "personal_db_text": db_context['personal'],
                "resume_db_text": db_context['resume']
            }

            # --- C. AI Execution (Gateway) ---
            prompt = factory.create_expert_prompt(eid, "GAP_EFFORT", context_data)
            
            # [MODIFIED] 傳入 Pydantic Schema
            # 告訴 Gateway: "我要這個格式，其他的都不要"
            result = gateway.generate(
                prompt, 
                validate_gap_effort, 
                schema=GapAnalysisReport
            )

            # --- D. Save & Store ---
            council_memory.save(raw_jd, eid, "GAP_EFFORT", result)
            current_gaps[eid] = result

            # 統計顯示
            gaps = result.get("gap_analysis", [])
            found_count = sum(1 for g in gaps if "FOUND" in g.get("evidence_in_personal_db", {}).get("status", ""))
            tqdm.write(colored(f"    👤 {eid}: Analyzed {len(gaps)} items. Evidence found: {found_count}", get_expert_color(eid)))

        except Exception as e:
            tqdm.write(colored(f"    ❌ {eid} Gap Analysis Failed: {e}", "red"))

    dossier['expert_council']['gap_analysis'] = current_gaps
    return dossier

# ==========================================
# 🚀 Main Controller (Orchestrator)
# ==========================================
def run_phase3_dynamic_execution():
    cprint("\n🏛️  [Phase 3] EXPERT COUNCIL: Dynamic Diagnosis Pipeline", "magenta", attrs=['bold', 'reverse'])
    
    # 1. 初始化共通工具 (只做一次)
    try:
        # Gateway 負責模型路由與重試
        gateway = SmartModelGateway(API_KEY)
        
        # Factory 負責 Prompt
        pf_root = os.path.abspath("src/agents")
        factory = PromptFactory(root_dir=pf_root)
        
    except Exception as e:
        cprint(f"❌ Init Failed: {e}", "red"); return



    # 2. 預載資料庫 (只做一次，傳遞給 Step 2 使用)
    cprint("🔌 Pre-loading Knowledge Base...", "white")
    # [MODIFIED] 直接呼叫 db_connector
    user_profile_str = db_connector.get_user_profile()
    
    db_context = {
        "personal": db_connector.get_personal_knowledge_context(),
        "resume": db_connector.get_resume_bullets_context()
    }
    cprint(f"📚 DB Loaded: Personal ({len(db_context['personal'])} chars), Resume ({len(db_context['resume'])} chars)", "green")

    # 3. 遍歷檔案
    files = glob.glob(os.path.join(DIR_PENDING, "*.json"))
    pbar = tqdm(files, desc="Processing Dossiers", unit="job")
    
    for filepath in pbar:
        # Load Dossier
        with open(filepath, 'r', encoding='utf-8') as f:
            dossier = json.load(f)

        company = dossier.get('basic_info', {}).get('company', 'Unknown')
        pbar.set_postfix(company=company[:10])
        tqdm.write(colored(f"\n🎯 Target: {company}", "white", attrs=['bold']))

        # 決定專家 (Routing)
        target_experts = get_target_experts(dossier)
        tqdm.write(colored(f"  route -> {', '.join(target_experts)}", "dark_grey"))
        print(f"Called experts {target_experts}")
        # input()

        # === 執行 Step 1: Skill Extraction ===
        dossier = _step1_skill_extraction(dossier, target_experts, gateway, factory, user_profile_str)
        
        # [Checkpoint Save] 中途存檔 (安全網)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(dossier, f, indent=2, ensure_ascii=False)

        # === 執行 Step 2: Gap Analysis ===
        dossier = _step2_gap_analysis(dossier, gateway, factory, db_context)

        # === [Checkpoint] 預留位置給未來的 Strategy Data ===
        # dossier = _step3_strategy_summary(...) 
        
        # [Final Save] 最終存檔
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(dossier, f, indent=2, ensure_ascii=False)

    cprint("\n🎉 Diagnosis Complete.", "green")

if __name__ == "__main__":
    run_phase3_dynamic_execution()