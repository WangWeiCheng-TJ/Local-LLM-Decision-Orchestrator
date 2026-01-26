import enum
from typing import List, Optional
from pydantic import BaseModel, Field

# ==========================================
# Common Enums
# ==========================================

class EffortLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKER = "BLOCKER"

class EvidenceStatus(str, enum.Enum):
    FOUND_STRONG = "FOUND_STRONG"
    FOUND_WEAK = "FOUND_WEAK"
    NOT_FOUND = "NOT_FOUND"

# ==========================================
# Phase 1: Skill Extraction (針對 JD 的客觀分析)
# ==========================================

class SkillAnalysis(BaseModel):
    hidden_bar: str = Field(description="The underlying high bar, specific technical constraint, or implicit requirement not explicitly stated but implied.")
    quote_from_jd: str = Field(description="The exact text or summarized context from the JD that justifies this skill.")

class SkillItem(BaseModel):
    topic: str = Field(description="The name of the skill, domain, or technology.")
    priority: str = Field(description="MUST_HAVE or NICE_TO_HAVE")
    analysis: SkillAnalysis

class SkillExtractionReport(BaseModel):
    """
    Phase 1 Output Root
    """
    required_skills: List[SkillItem] = Field(description="List of extracted skills from JD")


# ==========================================
# Phase 2: Gap Analysis (你的能力 vs JD)
# ==========================================

class Evidence(BaseModel):
    status: EvidenceStatus
    evidence_snippet: str = Field(description="Direct quote or summary of the evidence found in Cheat Sheet/Personal DB. If NOT_FOUND, explain why.")

class ResumeReusability(BaseModel):
    status: str = Field(description="EXACT_MATCH, CONCEPT_MATCH, or NO_MATCH")
    closest_existing_bullet: Optional[str] = Field(None, description="The matching bullet text from current resume, or null if no match.")

class EffortAssessment(BaseModel):
    level: EffortLevel
    strategy: str = Field(description="Strategy to fix this gap (e.g., 'Rewrite Bullet', 'Add Project', 'Study Concept').")
    estimated_action: str = Field(description="Concrete action item (e.g., 'Add quantization metrics to Bullet #3').")

class GapAnalysisItem(BaseModel):
    topic: str = Field(description="The skill name being analyzed (must match Phase 1 topic).")
    evidence_in_personal_db: Evidence
    resume_reusability: ResumeReusability
    effort_assessment: EffortAssessment

class GapAnalysisReport(BaseModel):
    """
    Phase 2 Output Root
    """
    gap_analysis: List[GapAnalysisItem] = Field(description="List of gap analysis results")


# ==========================================
# Phase 3: Strategic Advisor (總體戰略建議)
# ==========================================

class AdviceItem(BaseModel):
    topic: str = Field(description="The focus area (e.g., 'PhD Leverage', 'Skill Gap Mitigation', 'Narrative Arc').")
    rationale: str = Field(description="The strategic reasoning behind this advice. Why does this matter for this specific JD?")
    actionable_step: str = Field(description="Concrete instruction on what to edit in the resume or say in the interview.")
    priority: str = Field(description="CRITICAL, HIGH, or MEDIUM")

class AdvisorReport(BaseModel):
    """
    Phase 3 Output Root
    """
    strategic_advice: List[AdviceItem] = Field(description="List of strategic advice for resume tailoring")