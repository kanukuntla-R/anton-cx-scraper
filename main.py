from fastapi import FastAPI
from pydantic import BaseModel
from crawl4ai import AsyncWebCrawler

app = FastAPI()

class ScrapeRequest(BaseModel):
    url: str

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=req.url)
        return {
            "raw_text": result.markdown,
            "url": req.url
        }
