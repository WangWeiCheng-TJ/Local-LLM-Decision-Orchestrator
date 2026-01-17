import sys
import os
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.utils import safe_generate_json

class CouncilAgent:
    def __init__(self, model):
        self.model = model

    def deliberate(self, dossier: dict, full_candidate_context: str) -> dict:
        role = dossier['basic_info'].get('role')
        company = dossier['basic_info'].get('company')
        location = dossier['basic_info'].get('location', 'Unknown')
        jd_text = dossier.get('raw_content', '') 

        expert_roster = """
        AVAILABLE EXPERTS (ROSTER):
        1. 👔 **HR Gatekeeper**: Assesses culture fit, soft skills, and "red flags" in personality.
        2. ⚙️ **Tech Lead**: Assesses technical stack depth, coding standards, and hard skills.
        3. ♟️ **Strategist**: Assesses Location Tier (1/2/3), Tax/Salary ROI, and Company Stability.
        4. 🛂 **Visa Officer**: Assesses work permit feasibility (CRITICAL for non-EU/UK/US roles).
        5. 🔬 **Academic Reviewer**: Assesses publication quality, research relevance, and innovation depth.
        6. 🏗️ **System Architect**: Assesses engineering scalability, cloud/DevOps skills, and production readiness.
        7. 🦁 **Leadership Scout**: Assesses mentorship, team management, and cross-functional influence.
        8. 🚀 **Startup Veteran**: Assesses risk tolerance, equity potential, and "wearing multiple hats" (for small companies).
        """

        prompt = f"""
        You are the **Chairperson of the AI Career Council**.
        
        ### OBJECTIVE
        Perform a **Strict Evidence-Based Gap Analysis** for the Resume Writer.
        
        ### CRITICAL INSTRUCTION
        **DO NOT be lazy.** Do NOT assume a match just because a section exists (e.g., having a "Publications" section is NOT enough). 
        You must verify if the **specific content** required by the JD exists in the Candidate Inventory.
        
        *Example*: If JD asks for "Diffusion Models" and Candidate only has "GANs", that is a **GAP**, not a match.
        *Example*: If JD asks for "Team Lead" and Candidate only has "Mentored Intern", that is a **Partial Match (Reframing needed)**.

        ### CONTEXT
        **TARGET JOB**: {role} @ {company}
        **JD Requirements**:
        {jd_text}

        **CANDIDATE INVENTORY (The "Lego Box"):**
        {full_candidate_context}

        {expert_roster}

        ### MISSION
        
        **STEP 1: SUMMON EXPERTS**
        Select 6-8 experts.

        **STEP 2: THE "LEGO" TEST (Evidence Matching)**
        For each critical JD requirement, search the Inventory for a matching "block".
        
        * **Direct Match**: Explicit evidence found. (e.g., JD: "Transformers", Inventory: "Paper on Transformers").
        * **Indirect Match**: Related evidence found. (e.g., JD: "Azure", Inventory: "AWS").
        * **Missing**: No evidence found.

        **STEP 3: CALCULATE REWRITE EFFORT**
        * **Low (Assembly)**: We have exact information in the blocks. Writer just needs to select and order them.
        * **Medium (Reframing)**: We dont have information in the blocks, but we have information in the database. Writer needs to "spin" the narrative (e.g., emphasize Generalization capabilities of GANs to cover Diffusion gap).
        * **High (Creation)**: We are missing information in the blocks. Writer needs to hallucinate or heavily embellish (High Risk).

        **STEP 4: SECTION DIAGNOSTICS**
        For each resume section, dictate the strategy:
        - **Summary**: Needs to pivot identity?
        - **Work Experience**: Do we have the specific *stories* they want?
        - **Publications**: Do our papers cover their specific *research topics*? (If not, maybe hide this section or move to appendix).

        ### OUTPUT JSON:
        {{
            "active_experts": ["Expert A", "Expert B"],
            "tier_classification": "Tier 1 / Tier 2 / Tier 3",
            
            "evaluation_data": {{
                "matched_skills": ["List EXACT matches found in inventory"],
                "missing_critical_skills": ["List requirements with NO evidence"],
                
                "section_diagnostics": {{
                    "summary": {{ "action": "Keep/Tweak/Overhaul", "reason": "..." }},
                    "work_experience": {{ "action": "Keep/Tweak/Overhaul", "reason": "..." }},
                    "publications": {{ "action": "Keep/Tweak/Overhaul", "reason": "e.g., Papers are too theoretical, need to emphasize applied parts." }},
                    "projects": {{ "action": "Keep/Tweak/Overhaul", "reason": "..." }}
                }},
                
                "verdict": "High Potential / Stretch / Unqualified" 
            }},

            "expert_opinions": {{ "Expert A": "..." }},
            "strategy_memo": {{
                "key_insight": "...",
                "resume_tweak_focus": "..."
            }}
        }}
        """

        default = {
            "active_experts": ["Tech Lead"],
            "evaluation_data": {
                "matched_skills": [],
                "missing_critical_skills": [],
                "section_diagnostics": {},
                "verdict": "Stretch"
            },
            "expert_opinions": {},
            "tier_classification": "Tier 3"
        }
        
        return safe_generate_json(self.model, prompt, retries=3, default_output=default)