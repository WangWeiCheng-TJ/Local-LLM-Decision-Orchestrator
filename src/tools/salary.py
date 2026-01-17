from duckduckgo_search import DDGS
import time

class SalaryTool:
    def __init__(self, max_retries=3, retry_delay=20):
        """
        初始化 SalaryTool
        max_retries: 最大重試次數（遇到速率限制時）
        retry_delay: 重試前的等待時間（秒）
        """
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    def check_salary(self, role: str, company: str, location: str) -> str:
        keywords = f'"{company}" "{role}" salary "{location}" site:levels.fyi OR site:glassdoor.com'
        print(f"💰 Salary Tool Searching (v4.1.1): {keywords}...")

        # 重試機制
        for attempt in range(self.max_retries):
            try:
                results = []
                with DDGS() as ddgs:
                    # v4.1.1 的 text 方法參數比較少，把 backend 拿掉
                    search_gen = ddgs.text(keywords, max_results=5)
                    
                    for r in search_gen:
                        # v4.1.1 回傳的 key 通常是 'title', 'href', 'body'
                        title = r.get('title', '')
                        link = r.get('href', '')
                        body = r.get('body', '')
                        
                        if len(body) > 20:
                            results.append(f"- [{title}]({link}): {body}")
                        
                        if len(results) >= 3:
                            break

                if not results:
                    return f"No direct salary data found for {role} at {company}."
                
                return "\n".join(results)

            except Exception as e:
                error_str = str(e).lower()
                # 檢查是否是速率限制錯誤
                if "ratelimit" in error_str or "rate limit" in error_str:
                    if attempt < self.max_retries - 1:
                        # 還有重試機會，等待後重試
                        wait_time = self.retry_delay * (attempt + 1)  # 遞增等待時間：5s, 10s, 15s...
                        print(f"⚠️ Rate limit hit. Waiting {wait_time} seconds before retry ({attempt + 1}/{self.max_retries})...")
                        time.sleep(wait_time)
                        continue
                    else:
                        # 最後一次嘗試也失敗了，放棄
                        print(f"❌ Rate limit error after {self.max_retries} attempts. Giving up.")
                        return f"Salary search failed: Rate limit exceeded after {self.max_retries} attempts. Please try again later."
                else:
                    # 其他類型的錯誤（非速率限制），不重試，直接返回
                    print(f"❌ Salary Tool Error: {e}")
                    return f"Salary search failed: {str(e)}"
        
        # 如果所有重試都失敗了（理論上不會到這裡，但以防萬一）
        return f"Salary search failed: Maximum retries ({self.max_retries}) exceeded."