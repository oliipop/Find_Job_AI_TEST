import requests
import json
import random
import logging
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fake_useragent import UserAgent
import uvicorn

app = FastAPI(title="本地求职信息检索系统")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@app.get("/api/search")
async def search_jobs(
    query: str = Query(..., description="搜索关键词，如 Python"), 
    city: str = Query("101010100", description="城市代码，默认北京")
):
    """
    接收前端搜索请求，调用 BOSS直聘 API 并清洗数据返回
    """
    url = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"
    headers = {
        "User-Agent": UserAgent().random,
        "Referer": "https://www.zhipin.com/",
    }
    params = {"query": query, "city": city, "page": 1, "pageSize": 15}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            job_list = data.get("zpData", {}).get("jobList", [])
            results = []
            for job in job_list:
                results.append({
                    "company": job.get("companyName"),
                    "position": job.get("jobName"),
                    "salary": job.get("salaryDesc"),
                    "area": job.get("area"),
                    "experience": job.get("jobExperience"),
                    "degree": job.get("jobDegree"),
                    "link": f"https://www.zhipin.com/job_detail/{job.get('encryptJobId')}.html"
                })
            return {"code": 200, "data": results}
        else:
            return {"code": 400, "msg": "接口返回异常"}
    except Exception as e:
        logging.error(f"抓取失败: {e}")
        return {"code": 500, "msg": "网络请求失败"}

if __name__ == "__main__":
    print("🚀 后端服务已启动，请访问 http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)