import enum
from pydantic import BaseModel, Field
from typing import List, Optional

# 定義 Enum 讓 AI 只能選這些值，不能瞎掰
class EffortLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKER = "BLOCKER"

class EvidenceStatus(str, enum.Enum):
    FOUND_STRONG = "FOUND_STRONG"
    FOUND_WEAK = "FOUND_WEAK"
    NOT_FOUND = "NOT_FOUND"

# --- 子物件定義 ---
class Evidence(BaseModel):
    status: EvidenceStatus
    evidence_snippet: str = Field(description="Direct quote or summary of the evidence found in Personal DB")

class ResumeReusability(BaseModel):
    status: str = Field(description="EXACT_MATCH, CONCEPT_MATCH, or NO_MATCH")
    closest_existing_bullet: Optional[str] = None

class EffortAssessment(BaseModel):
    level: EffortLevel
    strategy: str = Field(description="Strategy to fix this gap (e.g., 'Rewrite', 'Pivot Angle', 'Embed')")
    estimated_action: str

class GapAnalysisItem(BaseModel):
    topic: str = Field(description="The skill name being analyzed")
    evidence_in_personal_db: Evidence
    resume_reusability: ResumeReusability
    effort_assessment: EffortAssessment

# --- 根物件定義 (這就是我們要的最終 JSON) ---
class GapAnalysisReport(BaseModel):
    gap_analysis: List[GapAnalysisItem]