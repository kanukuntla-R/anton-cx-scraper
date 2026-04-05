from fastapi import FastAPI
from pydantic import BaseModel
import subprocess
import sys
import httpx
import io

app = FastAPI()

class ScrapeRequest(BaseModel):
    url: str

@app.on_event("startup")
async def startup_event():
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
    subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"], check=False)

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    try:
        # PDF handling — download and extract text directly
        if req.url.lower().endswith(".pdf") or "pdf" in req.url.lower():
            return await scrape_pdf(req.url)
        
        # HTML page handling — use crawl4ai
        return await scrape_html(req.url)

    except Exception as e:
        return {"error": str(e), "raw_text": None}


async def scrape_pdf(url: str):
    try:
        import pypdf

        # Download the PDF
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

        # Extract text from PDF bytes
        pdf_file = io.BytesIO(response.content)
        reader = pypdf.PdfReader(pdf_file)

        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)

        raw_text = "\n\n".join(text_parts)

        return {
            "raw_text": raw_text,
            "url": url,
            "source_type": "pdf"
        }

    except Exception as e:
        return {"error": f"PDF extraction failed: {str(e)}", "raw_text": None}


async def scrape_html(url: str):
    try:
        from crawl4ai import AsyncWebCrawler
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            return {
                "raw_text": result.markdown,
                "url": url,
                "source_type": "html"
            }
    except Exception as e:
        return {"error": f"HTML scraping failed: {str(e)}", "raw_text": None}
