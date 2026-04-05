from fastapi import FastAPI
from pydantic import BaseModel
import subprocess
import sys
import httpx
import io
from typing import Optional

app = FastAPI()

# Known payer policy search URLs
PAYER_SEARCH_URLS = {
    "uhc": "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html",
    "unitedhealth": "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html",
    "united": "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html",
    "cigna": "https://www.cigna.com/healthcare-professionals/resources-for-health-care-professionals/clinical-payment-and-reimbursement-policies/medical-coverage-policies",
    "bcbs": "https://www.bcbsnc.com/content/providers/clinical-policy-bulletins/index.htm",
    "bluecross": "https://www.bcbsnc.com/content/providers/clinical-policy-bulletins/index.htm",
    "emblemhealth": "https://www.emblemhealth.com/providers/clinical-resources/medical-policies",
    "aetna": "https://www.aetna.com/health-care-professionals/clinical-policy-bulletins/medical-clinical-policy-bulletins.html",
}

class ScrapeRequest(BaseModel):
    url: Optional[str] = None
    drug_name: Optional[str] = None
    payer: Optional[str] = None

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
        # Mode 1: Direct URL provided
        if req.url:
            if req.url.lower().endswith(".pdf") or "pdf" in req.url.lower():
                return await scrape_pdf(req.url)
            else:
                return await scrape_html(req.url)

        # Mode 2: Drug name + payer search
        if req.drug_name and req.payer:
            return await search_and_scrape(req.drug_name, req.payer)

        return {"error": "Provide either a url or both drug_name and payer", "raw_text": None}

    except Exception as e:
        return {"error": str(e), "raw_text": None}


async def search_and_scrape(drug_name: str, payer: str):
    """Find policy page for a drug+payer combo and scrape it"""
    payer_key = payer.lower().strip()

    # Find the base URL for this payer
    base_url = None
    for key in PAYER_SEARCH_URLS:
        if key in payer_key or payer_key in key:
            base_url = PAYER_SEARCH_URLS[key]
            break

    if not base_url:
        return {
            "error": f"Payer '{payer}' not recognized. Supported: UHC, Cigna, BCBS, EmblemHealth, Aetna",
            "raw_text": None,
            "supported_payers": list(PAYER_SEARCH_URLS.keys())
        }

    # Scrape the payer's policy index page
    # This returns the full index — Claude or your teammate will find
    # the specific drug section from the raw text
    result = await scrape_html(base_url)

    if result.get("raw_text"):
        # Also try a Google-style search URL for the specific drug
        search_url = f"https://www.google.com/search?q={drug_name}+{payer}+medical+benefit+drug+policy+site:{get_payer_domain(payer_key)}"
        result["search_url"] = search_url
        result["payer"] = payer
        result["drug_name"] = drug_name
        result["payer_index_url"] = base_url

    return result


def get_payer_domain(payer: str) -> str:
    domains = {
        "uhc": "uhcprovider.com",
        "united": "uhcprovider.com",
        "cigna": "cigna.com",
        "bcbs": "bcbsnc.com",
        "bluecross": "bcbsnc.com",
        "aetna": "aetna.com",
        "emblemhealth": "emblemhealth.com",
    }
    for key in domains:
        if key in payer:
            return domains[key]
    return ""


async def scrape_pdf(url: str):
    try:
        import pypdf
        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        ) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

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
            "source_type": "pdf",
            "pages": len(reader.pages)
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
