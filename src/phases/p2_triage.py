import os
import glob
import json
import shutil
import sys
from dotenv import load_dotenv
from termcolor import colored, cprint
import google.generativeai as genai
from tqdm import tqdm

# === 路徑設定與引用 ===
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.agents.triage import TriageAgent
# from src.agents.profile_generator import ProfileGeneratorAgent 

# === CONFIG ===
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")
CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "/app/data/chroma_db")

DIR_DOSSIERS = "/app/data/processed/dossiers"
DIR_PENDING = "/app/data/processed/pending_council"
DIR_TRASH = "/app/data/processed/trash"
PATH_PROFILE = "/app/data/personal/profile.md"

os.makedirs(DIR_PENDING, exist_ok=True)
os.makedirs(DIR_TRASH, exist_ok=True)

aggressive_instruction = (
        f"\n\n[SYSTEM ERROR]: Your previous JSON output was REJECTED."
        f"\nReason: The experts gave lazy one-word explanations."
        f"\nCorrection: You MUST rewrite the 'note' field for ALL experts."
        f"\nRule: The 'note' must be a COMPLETE SENTENCE (at least 15 words) explaining the score."
        f"\nExample: Instead of 'Helpful', write 'Candidate's C++ experience aligns well with the latency requirements.'"
    )

def get_or_create_profile(model):
    """取得使用者設定檔，並支援即時編輯後重載"""
    if not os.path.exists(PATH_PROFILE):
        cprint(f"🔍 Profile not found. Mining Personal Database...", "cyan")
        try:
            generator = ProfileGeneratorAgent(model, CHROMA_PATH)
            content = generator.generate_profile()
            with open(PATH_PROFILE, 'w', encoding='utf-8') as f:
                f.write(content)
            cprint(f"🎉 Auto-generated profile!", "green")
        except Exception as e:
            cprint(f"❌ Failed to generate profile: {e}", "red")
            sys.exit(1)

    while True:
        with open(PATH_PROFILE, 'r', encoding='utf-8') as f:
            content = f.read()
        cprint("\n📋 === REVIEW YOUR TRIAGE STRATEGY === ", "cyan", attrs=['bold'])
        print(colored("-" * 40, "dark_grey"))
        print(colored(content, "white"))
        print(colored("-" * 40, "dark_grey"))
        cprint("💡 You can edit 'data/personal/profile.md' manually NOW.", "dark_grey")
        
        choice = (input(colored("\n❓ Proceed? [Y] / e (Edit & Reload) / q (Quit): ", "yellow")).strip().lower() or 'y')
        if choice == 'y':
            return content
        elif choice in ['e', 'edit']:
            cprint(f"⏸️  Program PAUSED. Edit: {PATH_PROFILE}", "magenta", attrs=['reverse'])
            input(colored("⌨️  Press [ENTER] when saved...", "magenta"))
            continue
        elif choice == 'q':
            sys.exit(0)

def run_triage():
    cprint("\n🚑 [Phase 2] FULL RECONNAISSANCE TRIAGE", "cyan", attrs=['bold', 'reverse'])
    
    if not API_KEY:
        cprint("❌ API Key missing.", "red")
        return

    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    user_profile_text = get_or_create_profile(model)
    agent = TriageAgent(model)
    
    files = glob.glob(os.path.join(DIR_DOSSIERS, "*_dossier.json"))
    if not files:
        cprint(f"😴 No dossiers found in {DIR_DOSSIERS}.", "yellow")
        return

    cprint(f"📂 Evaluating {len(files)} dossiers...", "white")
    pbar = tqdm(files, desc="🩺 Triaging", unit="job")
    stats = {"pass": 0, "fail": 0}

    for filepath in pbar:
        filename = os.path.basename(filepath)
        pbar.set_postfix(file=filename[:10])

        with open(filepath, 'r', encoding='utf-8') as f:
            dossier = json.load(f)
        
        role = dossier.get('basic_info', {}).get('role', 'Unknown')
        company = dossier.get('basic_info', {}).get('company', 'Unknown')

        try:
            # === 核心：專家會診轉診報告 ===
            result = agent.evaluate(dossier, user_profile_text)
            decision = result.get('decision', 'PASS').upper()
            reason = result.get('reason', 'No reason provided')
            referral = result.get('referral_analysis', {})
            
            dossier['triage_result'] = result # 保存完整報告

            if decision == "PASS":
                # 先檢查內容有沒有問題要重跑
                if len(referral.get("E1", {}).get('note', 'N/A')) < 20:
                    referral = agent.evaluate(dossier, user_profile_text, aggressive_instruction).get('referral_analysis', {})
                    print("Regenerate Referral Report")


                # 1. 視覺回饋：印出通過訊息
                tqdm.write(colored(f"\n✅ PASS: {company} - {role}", "green", attrs=['bold']))
                
                # 2. 印出全量專家建議 (不篩選，顯示 E1-E8)
                for i in range(1, 9):
                    eid = f"E{i}"
                    data = referral.get(eid, {})
                    score = data.get('relevance', 0)
                    note = data.get('note', 'N/A')
                    
                    # 根據權重上色
                    color = "cyan" if score >= 7 else "dark_grey"
                    icon = "🔥" if score >= 7 else "▫️"
                    tqdm.write(colored(f"   {icon} [{eid}] Rel: {score}/10 | {note}", color))
                
                target_dir = DIR_PENDING
                stats["pass"] += 1
            else:
                tqdm.write(colored(f"🗑️  FAIL: {company} - {role}", "red"))
                tqdm.write(colored(f"   Reason: {reason}", "dark_grey"))
                target_dir = DIR_TRASH
                stats["fail"] += 1

            # === 存檔與移動 ===
            target_path = os.path.join(target_dir, filename)
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(dossier, f, indent=2, ensure_ascii=False)

        except Exception as e:
            tqdm.write(colored(f"⚠️ Agent Error on {filename}: {e}", "red"))
            continue

    # 總結
    cprint("\n🎉 Phase 2 Complete.", "magenta", attrs=['bold'])
    cprint(f"   🗑️  Trashed: {stats['fail']}", "red")
    cprint(f"   ✅ Pending Council: {stats['pass']}", "green")

if __name__ == "__main__":
    run_triage()