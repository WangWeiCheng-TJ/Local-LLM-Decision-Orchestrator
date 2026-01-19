import json
import os
import sys
from jinja2 import Environment, FileSystemLoader

# ------------------------------------------------------------------
# Path Setup: 讓這個 script 能找到同目錄的 schemas_definitions 與上層模組
# ------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, current_dir)  # 同目錄的 schemas_definitions
sys.path.append(project_root)

from schemas_definitions import SKILL_SCHEMA, GAP_EFFORT_SCHEMA, ADVISOR_SCHEMA, EDITOR_SCHEMA

# # Import Schemas (我們剛才定案的憲法)
# try:
    
# except ImportError:
#     print("❌ Error: Cannot import schemas. Make sure 'schemas_definitions.py' exists.")
#     sys.exit(1)

class PromptFactory:
    def __init__(self, root_dir=None):
        """
        初始化：設定路徑並載入專家設定檔
        """
        self.root = root_dir if root_dir else project_root
        self.template_dir = os.path.join(self.root, "character_setting")
        self.config_path = os.path.join(self.root, "character_setting", "personas.json")

        # 1. 初始化 Jinja2 環境
        if not os.path.exists(self.template_dir):
            raise FileNotFoundError(f"Templates dir not found: {self.template_dir}")
        self.env = Environment(loader=FileSystemLoader(self.template_dir))
        
        # 2. 載入專家設定 (Personas)
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
            
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.personas = json.load(f)

    def create_expert_prompt(self, expert_id: str, mode: str, context_data: dict) -> str:
        """
        產生 Council Member (E1~E8) 的 Prompt
        mode: "SKILL" | "GAP_EFFORT" | "ADVISOR"
        """
        # A. 取得該專家的 Config
        expert_config = self.personas.get(expert_id)
        if not expert_config:
            raise ValueError(f"Expert ID '{expert_id}' not found in member_personas.json")

        # B. 準備渲染變數 (合併 Config + Context + Mode)
        render_vars = {
            **expert_config,  # 展開 role_name, philosophy, few_shot_examples...
            **context_data,   # 展開 job_title, raw_jd_text, user_profile...
            "mode": mode
        }

        # C. 根據 Mode 注入對應的 Schema (The Constitution)
        if mode == "SKILL":
            render_vars["skill_schema"] = SKILL_SCHEMA
        elif mode == "GAP_EFFORT":
            render_vars["gap_effort_schema"] = GAP_EFFORT_SCHEMA
        elif mode == "ADVISOR":
            render_vars["advisor_schema"] = ADVISOR_SCHEMA
        else:
            raise ValueError(f"Invalid mode: {mode}")

        # D. 渲染模板
        try:
            template = self.env.get_template("member_prompt.md.j2")
            return template.render(render_vars)
        except Exception as e:
            raise RuntimeError(f"Failed to render expert template: {e}")

    def create_editor_prompt(self, council_opinions: list, context_data: dict) -> str:
        """
        產生 Editor (主編) 的 Prompt
        """
        # A. 取得 Editor Config
        editor_config = self.personas.get("EDITOR")
        if not editor_config:
            # Fallback: 如果 json 裡沒寫 EDITOR，給個預設值以免 crash
            editor_config = {
                "role_name": "Editor-in-Chief",
                "role_icon": "✍️",
                "philosophy": "Synthesize and resolve conflicts."
            }

        # B. 準備變數
        render_vars = {
            **editor_config,
            **context_data,
            "council_opinions": council_opinions, # 這是 E1~E8 的分析報告列表
            "editor_schema": EDITOR_SCHEMA        # 注入 Editor Schema
        }

        # C. 渲染模板
        try:
            template = self.env.get_template("editor_prompt.md.j2")
            return template.render(render_vars)
        except Exception as e:
            raise RuntimeError(f"Failed to render editor template: {e}")

# ------------------------------------------------------------------
# 自我測試區塊 (Self-Test) - 增強版：會存檔
# ------------------------------------------------------------------
if __name__ == "__main__":
    print(f"🚀 Initializing PromptFactory at: {project_root}")
    
    # 建立一個測試輸出的資料夾
    output_dir = os.path.join(project_root, "test_outputs")
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        factory = PromptFactory()
        
        # --- 測試 1: E2 (Tech Lead) 的 Skill Extraction ---
        print("\n[Test 1] Generating E2 Prompt (SKILL Mode)...")
        mock_context_jd = {
            "job_title": "Senior Rust Engineer",
            "company_name": "NVIDIA",
            "raw_jd_text": "Must have deep knowledge of Rust lifetimes and CUDA."
        }
        prompt_e2 = factory.create_expert_prompt("E2", "SKILL", mock_context_jd)
        
        # 存檔
        with open(os.path.join(output_dir, "test_E2_skill_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt_e2)
        print(f"✅ Saved to: test_outputs/test_E2_skill_prompt.txt")

        # --- 測試 2: E2 的 Gap Analysis ---
        print("\n[Test 2] Generating E2 Prompt (GAP_EFFORT Mode)...")
        mock_context_gap = {
            "user_profile_text": "PhD in AI, C++ Expert.",
            "skill_list_json": "[{\"id\": \"skill_rust\", \"topic\": \"Rust\"}]",
            "personal_db_text": "GitHub: toy-rust-repo...",
            "resume_db_text": "Bullet: Managed C++ memory..."
        }
        prompt_gap = factory.create_expert_prompt("E2", "GAP_EFFORT", mock_context_gap)
        
        # 存檔
        with open(os.path.join(output_dir, "test_E2_gap_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt_gap)
        print(f"✅ Saved to: test_outputs/test_E2_gap_prompt.txt")
        
        # --- 測試 3: Editor ---
        print("\n[Test 3] Generating Editor Prompt...")
        mock_opinions = [
            {"role_name": "Tech Lead", "expert_id": "E2", "focus_area": "Hard Skills", "action_plan_json": "..."},
            {"role_name": "HR", "expert_id": "E1", "focus_area": "Soft Skills", "action_plan_json": "..."}
        ]
        mock_context_editor = {"job_title": "Rust Eng", "user_profile_summary": "Strong C++"}
        
        prompt_editor = factory.create_editor_prompt(mock_opinions, mock_context_editor)
        
        # 存檔
        with open(os.path.join(output_dir, "test_Editor_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt_editor)
        print(f"✅ Saved to: test_outputs/test_Editor_prompt.txt")
        
        print("\n🎉 測試完成！請去 'test_outputs' 資料夾檢查生成的 Prompt 內容。")

    except Exception as e:
        print(f"\n❌ Test Failed: {e}")