# -------------------------------------------------------------------------
# Expert Council - Schema Definitions (The Constitution)
# -------------------------------------------------------------------------

# 🟢 Phase 1: Skill Extraction (萃取標準)
# Goal: Define the "Bar" and "Hidden Rules" from JD.
SKILL_SCHEMA = """
{
  "expert_id": "string (e.g., E2)",
  "required_skills": [
    {
      "id": "string (unique id, e.g., skill_rust_01)",
      "topic": "string (e.g., Rust Memory Safety)",
      "priority": "MUST_HAVE | NICE_TO_HAVE | BLOCKER",
      "description": "string (brief summary)",
      
      "analysis": {
         "hidden_bar": "string (Expert insight: What is the ACTUAL unwritten standard?)",
         "quote_from_jd": "string (Evidence from original text)"
      }
    }
  ]
}
"""

# 🔵 Phase 2: Gap & Effort Analysis (診斷差距 - Writing Cost Logic)
# Logic Update:
# - Check 1 (Personal DB): Do I have the raw evidence?
# - Check 2 (Resume DB): Do I have a sentence I can recycle?
#   - Effort NONE: Exact/Good match.
#   - Effort LOW: Concept match but wrong Angle (e.g., Research -> Engineering).
#   - Effort MEDIUM: No sentence, must write from scratch using Evidence.
#   - Effort HIGH: No evidence, must study/learn.

GAP_EFFORT_SCHEMA = """
{
  "expert_id": "string",
  "gap_analysis": [
    {
      "skill_ref_id": "string (must match Phase 1 ID)",
      "topic": "string",
      
      # 1. Evidence Check (Capability: Can I do it?)
      "evidence_in_personal_db": {
        "status": "FOUND_STRONG | FOUND_WEAK | NOT_FOUND",
        "evidence_snippet": "string (Source material found in papers/github/notes)",
        "gap_conclusion": "SKILL_CONFIRMED | SKILL_MISSING" 
      },

      # 2. Reusability Check (Writing Cost: Do I have a draft?)
      "resume_reusability": {
        "status": "EXACT_MATCH | CONCEPT_MATCH | NO_MATCH",
        "closest_existing_bullet": "string (The old sentence found, or null)",
        "modification_required": "TWEAK_ONLY | TWIST_ANGLE | NEW_CREATION"
      },

      # 3. Effort Level (Action Cost)
      "effort_assessment": {
        "level": "NONE | LOW | MEDIUM | HIGH",
        "score": 1-10, 
        "reason": "string (e.g., 'Found bullet point but needs to shift from Research angle to Engineering angle')"
      },
      
      # 4. Action for Advisor
      "fixing_strategy": {
        "action": "KEEP_AS_IS | TWEAK_KEYWORDS | TWIST_VIEWPOINT | WRITE_FROM_SCRATCH | IGNORE",
        "instruction": "string (e.g., 'Twist the PyTorch loader experience to emphasize Latency Optimization instead of Novelty')"
      }
    }
  ]
}
"""

# 🟣 Phase 3: Advisor Mode (開立處方 - Actionable Content)
# Goal: Produce the actual text or task list.
ADVISOR_SCHEMA = """
{
  "expert_id": "string",
  "action_plan": [
    {
      "related_skill_id": "string",
      "topic": "string",
      "action_type": "RESUME_REWRITE | COVER_LETTER_HOOK | PORTFOLIO_ADDITION | LEARNING_TASK",
      "priority": "HIGH | MEDIUM | LOW",
      
      "content_suggestion": {
        "before_text": "string (Original text if rewriting, else null)",
        "after_text": "string (THE DRAFT: The actual polished content ready to use)",
        "rationale": "string (Why this change? e.g., 'Added quantitative metrics to satisfy E2')"
      }
    }
  ]
}
"""

# ✍️ Phase 4: Editor Mode (總編輯整合 - Final Synthesis)
# Goal: Resolve conflicts and merge drafts.
EDITOR_SCHEMA = """
{
  "editor_summary": {
    "decision": "READY_TO_SUBMIT | NEEDS_REVISION | BLOCKED",
    "strategy_explanation": "string (Explain conflict resolution, e.g., 'Prioritized E2 over E1 due to tech focus')"
  },
  
  "final_action_items": [
    {
      "id": "action_01",
      "target_section": "Experience | Skills | Summary | Projects",
      "action": "REWRITE | ADD | DELETE | REORDER",
      "final_content": "string (The final, merged, and polished text)",
      "sources": ["E2", "E5"] // Which experts contributed to this specific point?
    }
  ],

  "blind_spot_warnings": [
    "string (Crucial warnings extracted from experts, e.g., 'Visa risk detected by E4')"
  ]
}
"""