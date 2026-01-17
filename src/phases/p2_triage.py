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
# 確保能引用 src 下的模組
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.agents.triage import TriageAgent
from src.agents.profile_generator import ProfileGeneratorAgent 

# === CONFIG ===
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")
CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "/app/data/chroma_db")

# 資料流向
PATH_PROFILE = "/app/data/personal/profile.md"       # 核心設定檔 (Source of Truth)
DIR_DOSSIERS = "/app/data/processed/dossiers"        # 輸入：Phase 1 產出的 JD
DIR_PENDING = "/app/data/processed/pending_council"  # 輸出：通過 Triage
DIR_TRASH = "/app/data/processed/trash"              # 輸出：垃圾桶

# 建立目錄
os.makedirs(DIR_PENDING, exist_ok=True)
os.makedirs(DIR_TRASH, exist_ok=True)
os.makedirs(os.path.dirname(PATH_PROFILE), exist_ok=True)

# src/phases/p2_triage.py
# (前段 imports 保持不變) ...

def get_or_create_profile(model):
    """
    取得使用者設定檔，並支援即時編輯後重載。
    """
    
    # 1. 如果完全沒檔案，先自動生成一份 (以免後面讀檔報錯)
    if not os.path.exists(PATH_PROFILE):
        cprint(f"🔍 Profile not found. Mining Personal Database ({CHROMA_PATH})...", "cyan")
        try:
            generator = ProfileGeneratorAgent(model, CHROMA_PATH)
            content = generator.generate_profile()
            with open(PATH_PROFILE, 'w', encoding='utf-8') as f:
                f.write(content)
            cprint(f"🎉 Auto-generated profile based on your DB records!", "green")
        except Exception as e:
            cprint(f"❌ Failed to generate profile: {e}", "red")
            sys.exit(1)

    # 2. 進入確認迴圈 (Loop until satisfied)
    while True:
        # 重新讀取檔案 (確保讀到你剛剛編輯過的內容)
        with open(PATH_PROFILE, 'r', encoding='utf-8') as f:
            content = f.read()

        # 顯示內容
        cprint("\n📋 === REVIEW YOUR TRIAGE STRATEGY === ", "cyan", attrs=['bold'])
        print(colored("-" * 40, "dark_grey"))
        print(colored(content, "white"))
        print(colored("-" * 40, "dark_grey"))
        
        cprint("💡 You can edit 'data/personal/profile.md' manually NOW.", "dark_grey")
        
        # 詢問使用者
        choice = input(colored("\n❓ Proceed? [y (Yes) / e (Edit & Reload) / q (Quit)]: ", "yellow")).strip().lower()
        
        if choice == 'y':
            cprint("✅ Profile confirmed. Starting Triage...", "green")
            return content
            
        elif choice == 'e' or choice == 'edit':
            cprint(f"⏸️  Program PAUSED.", "magenta", attrs=['bold', 'reverse'])
            cprint(f"👉 Please open and edit: {PATH_PROFILE}", "white")
            input(colored("⌨️  Press [ENTER] when you have saved your changes...", "magenta"))
            cprint("🔄 Reloading profile...", "cyan")
            continue # 跳回迴圈開頭，重新讀檔
            
        elif choice == 'q':
            cprint("🛑 Operation aborted by user.", "red")
            sys.exit(0)
        
        else:
            cprint("❌ Invalid choice.", "red")


def run_triage():
    cprint("\n🚑 [Phase 2] TRIAGE AGENT ACTIVATED", "cyan", attrs=['bold', 'reverse'])
    
    if not API_KEY:
        cprint("❌ API Key missing. Check .env", "red")
        return

    # 1. 初始化模型
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    
    # 2. 獲取並確認 Profile (這是最關鍵的一步)
    user_profile_text = get_or_create_profile(model)

    # 3. 初始化 Agent
    agent = TriageAgent(model)
    
    # 4. 掃描檔案
    files = glob.glob(os.path.join(DIR_DOSSIERS, "*_dossier.json"))
    if not files:
        cprint(f"😴 No dossiers found in {DIR_DOSSIERS}. Run Phase 1 first.", "yellow")
        return

    cprint(f"📂 Evaluating {len(files)} dossiers...", "white")

    # 5. 開始迴圈
    pbar = tqdm(files, desc="🩺 Triaging", unit="job")
    
    stats = {"pass": 0, "fail": 0}

    for filepath in pbar:
        filename = os.path.basename(filepath)
        pbar.set_postfix(file=filename[:10])

        # 讀取 Phase 1 的資料
        with open(filepath, 'r', encoding='utf-8') as f:
            dossier = json.load(f)
        
        role = dossier.get('basic_info', {}).get('role', 'Unknown')
        company = dossier.get('basic_info', {}).get('company', 'Unknown')

        # === 核心：Agent 判斷 ===
        try:
            result = agent.evaluate(dossier, user_profile_text)
        except Exception as e:
            tqdm.write(colored(f"⚠️ Agent Error on {filename}: {e}", "red"))
            continue

        decision = result.get('decision', 'PASS').upper()
        reason = result.get('reason', 'No reason provided')
        
        # 將結果寫回 JSON (留下審計紀錄)
        dossier['triage_result'] = result

        # === 處置 (移動檔案) ===
        if decision == "FAIL":
            target_dir = DIR_TRASH
            status_icon = "🗑️ FAIL"
            color = "red"
            stats["fail"] += 1
        else:
            target_dir = DIR_PENDING
            status_icon = "✅ PASS"
            color = "green"
            stats["pass"] += 1

        # 移動並覆蓋
        target_path = os.path.join(target_dir, filename)
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(dossier, f, indent=2, ensure_ascii=False)
        
        # 刪除原始檔案
        # os.remove(filepath)

        # Log
        msg = f"{status_icon}: {company} - {role}"
        tqdm.write(colored(msg, color))
        if decision == "FAIL":
             tqdm.write(colored(f"   Reason: {reason}", "dark_grey"))

    # 6. 總結
    cprint("\n🎉 Phase 2 Complete.", "magenta", attrs=['bold'])
    cprint(f"   🗑️  Trashed: {stats['fail']}", "red")
    cprint(f"   ✅ Pending Council: {stats['pass']}", "green")

if __name__ == "__main__":
    run_triage()