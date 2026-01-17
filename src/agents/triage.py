import sys
import os
import json

# 引用工具
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.utils import safe_generate_json

class TriageAgent:
    def __init__(self, model):
        self.model = model

    def evaluate(self, dossier: dict, user_profile: str) -> dict:
        """
        Phase 2: Triage (檢傷分類)
        任務：拿使用者的 User Profile 去撞 JD，不符合硬指標的直接丟 Trash。
        """
        
        # [修正 1] 補上 location，這對 Triage 很重要
        jd_summary = {
            "role": dossier['basic_info'].get('role'),
            "company": dossier['basic_info'].get('company'),
            "location": dossier['basic_info'].get('location', 'Unknown'), 
            "level": dossier['basic_info'].get('experience_level'),
            "tech_stack": dossier['basic_info'].get('tech_stack'),
            # "salary_report": dossier['intelligence_report'] # 暫時拿掉，Phase 2 先不看薪水，避免太複雜
        }

        prompt = f"""
        You are a Triage Officer responsible for filtering job applications based on a candidate's hard constraints.
        
        ### 1. THE CANDIDATE (User Profile):
        {user_profile}
        
        ### 2. THE JOB (JD Summary):
        {json.dumps(jd_summary, indent=2)}
        
        ### YOUR TASK:
        Compare the Job against the Candidate's Hard Constraints.
        
        ### DECISION RULES:
        - **FAIL**: If the job violates ANY **hard constraint** defined in the User Profile.
          - Examples of FAIL: 
            - Location mismatch (e.g., job requires US onsite but candidate is in EU).
            - Tech Stack mismatch (e.g., job is pure Java/Frontend).
            - Level mismatch (e.g., Job is VP/Director or Intern).
        - **PASS**: If the job fits the profile OR is ambiguous (err on the side of caution).
        
        ### CRITICAL NOTE:
        - If the candidate has a PhD, **DO NOT FAIL** jobs that require a PhD. That is a match.
        
        ### OUTPUT JSON:
        {{
            "decision": "PASS" or "FAIL",
            "reason": "Brief reason (max 1 sentence).",
            "risk_score": 0  // 0-100 (High score = High Risk of mismatch)
        }}
        """

        default_output = {
            "decision": "PASS", 
            "reason": "Agent execution error, defaulting to PASS.",
            "risk_score": 50
        }

        return safe_generate_json(self.model, prompt, retries=3, default_output=default_output)