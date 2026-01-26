import json
import os
import glob
import numpy as np
from collections import defaultdict, Counter

# Algorithms
from sklearn.cluster import DBSCAN, HDBSCAN, AgglomerativeClustering
from sklearn.metrics.pairwise import euclidean_distances

import google.generativeai as genai
from termcolor import colored, cprint
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=API_KEY)

DIR_INPUT = "/app/data/processed/pending_council"
DIR_OUTPUT = "/app/data/processed/battle_plan"
os.makedirs(DIR_OUTPUT, exist_ok=True)

class WeightedClusterStrategy:
    def __init__(self):
        self.jobs = []
        self.vectors = []
        
        # --- Config from Env ---
        self.METHOD = os.getenv("CLUSTERING_METHOD", "DBSCAN").upper()
        self.ALPHA_MUST = float(os.getenv("WEIGHT_MUST", "0.75"))
        self.ALPHA_NICE = float(os.getenv("WEIGHT_NICE", "0.25"))
        
        # Manual Overrides (Optional)
        if self.METHOD == "DBSCAN":
            clu_env = os.getenv("CLUSTERING_EPS")
            if clu_env and clu_env.lower() != "auto":
                clu_env = float(clu_env)
            else:
                clu_env = None # None 代表之後要 Auto-Tune
        elif self.METHOD == "AGGLOMERATIVE":
            clu_env = os.getenv("CLUSTERING_THRESHOLD")
            if clu_env and clu_env.lower() != "auto":
                clu_env = float(clu_env)
            else:
                clu_env = None # None 代表之後要 Auto-Tune
        else:
            clu_env = None
        
        self.MANUAL_EPS = clu_env
        self.MANUAL_THRESH = clu_env

        self.FATAL_KEYWORDS = [
            # "penetration testing", "manual testing", "manual exploitation", 
            # "incident response", "soc analyst", "on-call", "technical support", 
            # "sales engineer", "frontend", "php", "wordpress"
        ]

    def load_jobs(self):
        files = glob.glob(os.path.join(DIR_INPUT, "*.json"))
        cprint(f"📦 Loading {len(files)} dossiers...", "cyan")
        
        for fpath in files:
            with open(fpath, 'r', encoding='utf-8') as f:
                job = json.load(f)
                # 簡易 Fatal 過濾
                gaps = job.get('expert_council', {}).get('gap_analysis', {})
                is_fatal = False
                for _, data in gaps.items():
                    for g in data.get('gap_analysis', []):
                        if g['effort_assessment']['level'] == 'HIGH':
                            if any(k in g['topic'].lower() for k in self.FATAL_KEYWORDS):
                                is_fatal = True; break
                            if "visa" in g['topic'].lower(): is_fatal = True; break
                    if is_fatal: break
                
                if not is_fatal: self.jobs.append(job)

    def extract_separated_features(self, job):
        council = job.get('expert_council', {})
        skill_analysis = council.get('skill_analysis', {})
        must_feats = []
        nice_feats = []
        for _, data in skill_analysis.items():
            for skill in data.get('required_skills', []):
                p = skill.get('priority')
                t = skill['topic']
                if p == 'MUST_HAVE': must_feats.append(t)
                elif p == 'NICE_TO_HAVE': nice_feats.append(t)
        role = job.get('basic_info', {}).get('role', '')
        return f"{role}, " + ", ".join(set(must_feats)), ", ".join(set(nice_feats))

    def calculate_job_effort(self, job):
        council = job.get('expert_council', {})
        gap_analysis = council.get('gap_analysis', {})
        total_cost = 0; critical_gaps = []
        for _, data in gap_analysis.items():
            for gap in data.get('gap_analysis', []):
                effort = gap['effort_assessment']['level']
                if effort == 'HIGH': total_cost += 10; critical_gaps.append(gap['topic'])
                elif effort == 'MEDIUM': total_cost += 3
                else: total_cost += 1
        return total_cost, list(set(critical_gaps))

    def _auto_tune_param(self, vectors, percentile=75):
        """
        自動計算最佳距離參數 (EPS 或 Threshold)
        """
        if len(vectors) < 2: return 0.5
        dists = euclidean_distances(vectors)
        sorted_dists = np.sort(dists, axis=1)
        nearest_neighbor_dists = sorted_dists[:, 1]
        
        val = np.percentile(nearest_neighbor_dists, percentile)
        return max(0.3, min(val, 0.9)) # Clamp between 0.3 and 0.9

    def _run_clustering_algo(self, vectors):
        """
        [策略模式] 根據 ENV 選擇演算法
        """
        labels = []
        
        # === Method 1: DBSCAN (Default) ===
        if self.METHOD == "DBSCAN":
            eps = float(self.MANUAL_EPS) if self.MANUAL_EPS else self._auto_tune_param(vectors, percentile=75)
            cprint(f"🧩 Running DBSCAN (eps={eps:.3f}, min_samples=1)...", "yellow")
            
            clusterer = DBSCAN(eps=eps, min_samples=1, metric='euclidean')
            labels = clusterer.fit_predict(vectors)

        # === Method 2: HDBSCAN (Hierarchical Density) ===
        # 優點: 不用調 eps，自動處理疏密不均。
        # 缺點: 資料少時容易全部判為 Noise (-1)，所以 min_cluster_size 要設很小
        elif self.METHOD == "HDBSCAN":
            cprint(f"🧩 Running HDBSCAN (min_cluster_size=2)...", "yellow")
            
            clusterer = HDBSCAN(min_cluster_size=2, min_samples=1, metric='euclidean')
            labels = clusterer.fit_predict(vectors)

        # === Method 3: Agglomerative (Hierarchical Bottom-Up) ===
        # 優點: 強制分群 (不會有 Noise -1)，結構清晰。
        # 缺點: 如果閾值設不好，會切得太碎或太粗。
        elif self.METHOD == "AGGLOMERATIVE":
            thresh = float(self.MANUAL_THRESH) if self.MANUAL_THRESH else self._auto_tune_param(vectors, percentile=85)
            cprint(f"🧩 Running Agglomerative (threshold={thresh:.3f})...", "yellow")
            
            # 注意: Agglomerative 預設沒有 predict 方法，直接 fit_predict
            clusterer = AgglomerativeClustering(
                n_clusters=None, # 自動決定群數
                distance_threshold=thresh, 
                metric='euclidean', 
                linkage='average'
            )
            labels = clusterer.fit_predict(vectors)
            
        else:
            cprint(f"❌ Unknown method: {self.METHOD}, falling back to DBSCAN", "red")
            return self._run_clustering_algo(vectors) # Recursive fallback (careful with loop)

        return labels

    def process_data(self):
        if not self.jobs: return

        must_texts = []
        nice_texts = []
        for job in self.jobs:
            m, n = self.extract_separated_features(job)
            must_texts.append(m)
            nice_texts.append(n if n else "General")

        cprint("🧠 Generating Dual Embeddings...", "yellow")
        try:
            resp_m = genai.embed_content(model="models/text-embedding-004", content=must_texts, task_type="clustering")
            vec_m = np.array(resp_m['embedding'])
            
            resp_n = genai.embed_content(model="models/text-embedding-004", content=nice_texts, task_type="clustering")
            vec_n = np.array(resp_n['embedding'])
            
            cprint(f"⚗️  Mixing Vectors: {self.ALPHA_MUST*100}% Must + {self.ALPHA_NICE*100}% Nice", "cyan")
            self.vectors = (self.ALPHA_MUST * vec_m) + (self.ALPHA_NICE * vec_n)
            
        except Exception as e:
            cprint(f"❌ Embedding failed: {e}", "red")
            return

        # 呼叫演算法調度器
        labels = self._run_clustering_algo(self.vectors)
        
        for i, job in enumerate(self.jobs):
            job['cluster_id'] = int(labels[i])
            cost, crits = self.calculate_job_effort(job)
            job['effort_cost'] = cost
            job['critical_gaps'] = crits

    def analyze_clusters(self):
        clusters = defaultdict(list)
        for job in self.jobs:
            clusters[job.get('cluster_id', -1)].append(job)

        report_data = []
        for cid, job_list in clusters.items():
            if cid == -1: 
                # HDBSCAN 的 Noise 
                report_data.append({
                    "cluster_id": -1, "size": len(job_list), "avg_effort": 0, "roi_score": 0,
                    "common_gaps": [], "flavors": ["Uncategorized Noise"], "jobs": job_list
                })
                continue

            count = len(job_list)
            avg_effort = np.mean([j['effort_cost'] for j in job_list])
            all_gaps = [g for j in job_list for g in j['critical_gaps']]
            common_gaps = [item for item, c in Counter(all_gaps).items() if c > 1]

            all_nices = []
            for j in job_list:
                _, n = self.extract_separated_features(j)
                all_nices.extend([x.strip() for x in n.split(',') if x.strip()])
            top_flavors = [k for k,v in Counter(all_nices).most_common(3)]

            roi_score = (count * 10) / (avg_effort + 1)

            report_data.append({
                "cluster_id": cid,
                "size": count,
                "avg_effort": round(avg_effort, 1),
                "roi_score": round(roi_score, 2),
                "common_gaps": common_gaps,
                "flavors": top_flavors,
                "jobs": job_list
            })

        # 排除 noise 後排序，最後再把 noise 加回去顯示
        valid_clusters = [c for c in report_data if c['cluster_id'] != -1]
        noise_clusters = [c for c in report_data if c['cluster_id'] == -1]
        valid_clusters.sort(key=lambda x: x['roi_score'], reverse=True)
        
        return valid_clusters + noise_clusters

    def execute(self):
        self.load_jobs()
        self.process_data()
        report = self.analyze_clusters()
        self._print_battle_plan(report)
        with open(os.path.join(DIR_OUTPUT, "final_battle_plan.json"), 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    def _print_battle_plan(self, clusters):
        cprint(f"\n⚔️  STRATEGY REPORT (Method: {self.METHOD}) ⚔️", "white", attrs=['bold', 'reverse'])
        
        for idx, cluster in enumerate(clusters):
            cid = cluster['cluster_id']
            if cid == -1:
                cprint(f"\n🗑️  Noise / Uncategorized ({cluster['size']} Jobs)", "dark_grey", attrs=['bold'])
            else:
                cprint(f"\n🎯 Cluster {cid} (Priority #{idx+1})", "cyan", attrs=['bold'])
                print(f"   📊 Size: {cluster['size']} | ROI: {cluster['roi_score']} | Flavor: {', '.join(cluster['flavors'])}")
                if cluster['common_gaps']: cprint(f"   ⚠️  Fix: {', '.join(cluster['common_gaps'])}", "red")
            
            print("   -----------------------------------")
            for job in cluster['jobs'][:5]:
                print(f"   - {job['basic_info']['company']}: {job['basic_info']['role']} (Cost: {job['effort_cost']})")
            if len(cluster['jobs']) > 5: print(f"     ... {len(cluster['jobs'])-5} more")

if __name__ == "__main__":
    WeightedClusterStrategy().execute()