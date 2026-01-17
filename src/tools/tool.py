import random
import time
import os

from src.tools.arXiv import ArxivTool
from src.tools.salary import SalaryTool

USE_MOCK_TOOLS = os.getenv("USE_MOCK_TOOLS", "False").lower() == "true"

class MockSalaryTool:
    """模擬查詢市場薪資行情的工具"""
    
    def __init__(self):
        # 預設一些基準數據，讓 Mock 看起來真實一點
        self.base_rates = {
            "research scientist": (180000, 250000),
            "machine learning engineer": (160000, 230000),
            "software engineer": (140000, 200000),
            "data scientist": (130000, 190000),
            "postdoc": (60000, 85000)
        }

    def check_salary(self, role_title: str, location: str = "US", company: str = "Unknown Company") -> str:
        """
        Input: Role Title (e.g., 'Senior Research Scientist'), Location
        Output: Structured string with salary range and market sentiment.
        """
        # 模擬 API 延遲
        # time.sleep(0.5) 
        
        role_key = role_title.lower()
        found_range = None

        # 簡單的關鍵字匹配
        for key, val in self.base_rates.items():
            if key in role_key:
                found_range = val
                break
        
        if not found_range:
            # 沒對到的話，給一個通用範圍
            found_range = (100000, 150000)

        # 加入隨機波動 (Mock 的靈魂)
        low = int(found_range[0] * random.uniform(0.9, 1.1))
        high = int(found_range[1] * random.uniform(0.9, 1.2))
        
        return f"[MockSalaryTool] Market Range for '{role_title}' in {location}: ${low:,} - ${high:,} / year. (Confidence: High)"


class MockArxivTool:
    """模擬查詢公司近期發表的 ArXiv 論文"""

    def __init__(self):
        # 預設一些公司的假論文數據 (Research Scientist 面試亮點)
        self.paper_database = {
            "google": [
                "Attention Is All You Need (Refresher)",
                "Gemini: A Family of Highly Capable Multimodal Models"
            ],
            "openai": [
                "Language Models are Few-Shot Learners",
                "GPT-4 Technical Report"
            ],
            "meta": [
                "Llama 2: Open Foundation and Chat Models",
                "Segment Anything"
            ],
            "nvidia": [
                "Improving Video Generation with Diffusion Models",
                "Real-time Neural Rendering"
            ]
        }

    def search_papers(self, company_name: str, keywords: list = None) -> str:
        """
        Input: Company Name, Keywords (optional)
        Output: List of relevant papers or 'No recent papers found'.
        """
        # time.sleep(0.5)
        
        company_key = company_name.lower()
        papers = []

        # 模擬搜尋邏輯
        for key, val in self.paper_database.items():
            if key in company_key:
                papers = val
                break
        
        if papers:
            # 隨機挑 1-2 篇展示
            selected = random.sample(papers, k=min(len(papers), 2))
            formatted_list = ", ".join([f"'{p}'" for p in selected])
            return f"[MockArxivTool] Found recent papers by {company_name}: {formatted_list}. (Relevance: High)"
        else:
            return f"[MockArxivTool] No direct ArXiv matches found for {company_name} in the last 12 months. (Might be stealth mode or non-publishing role)"

# 簡單的工廠模式，方便外部呼叫
class ToolRegistry:
    def __init__(self):
        if USE_MOCK_TOOLS==True:
            self.salary_tool = MockSalaryTool()
        else:
            self.salary_tool = SalaryTool(retry_delay=20)
        # self.arxiv_tool = MockArxivTool()
        self.arxiv_tool = ArxivTool()

    def run_tools(self, jd_data: dict) -> str:
        role = jd_data.get('role', 'Unknown Role')
        company = jd_data.get('company', '')
        location = jd_data.get('location', 'US')
        
        # [關鍵修正] 這裡要從 parser 的結果中抓出 keywords
        # 如果 parser 沒抓到，就給個空串列避免報錯
        keywords = jd_data.get('keywords', []) 
        
        # 1. 查薪水
        salary_info = self.salary_tool.check_salary(role, company, location)
        
        # 2. 查論文 (這裡記得要傳入兩個參數！)
        arxiv_info = self.arxiv_tool.search_papers(company, keywords)

        return f"""
### 🛠 External Intelligence Report
- **Market Salary Search Results**:
{salary_info}

- **Research Activity (ArXiv)**:
{arxiv_info}
--------------------------------------------------
"""