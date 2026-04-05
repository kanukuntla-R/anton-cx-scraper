from fastapi import FastAPI
from pydantic import BaseModel
import subprocess
import sys

app = FastAPI()

class ScrapeRequest(BaseModel):
    url: str

@app.on_event("startup")
async def startup_event():
    # Install playwright browsers on startup
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
    subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"], check=False)

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    try:
        from crawl4ai import AsyncWebCrawler
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=req.url)
            return {
                "raw_text": result.markdown,
                "url": req.url
            }
    except Exception as e:
        return {"error": str(e), "raw_text": None}
