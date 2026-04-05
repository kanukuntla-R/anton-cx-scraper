from fastapi import FastAPI
from pydantic import BaseModel
import subprocess
import sys
import httpx
import io
from typing import Optional

app = FastAPI()

# Direct policy library URLs per payer
PAYER_POLICY_URLS = {
    "uhc": "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html",
    "united": "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html",
    "cigna": "https://static.cigna.com/assets/chcp/pdf/coveragePolicies/medical/index_of_medical_coverage_policies.pdf",
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
        # Mode 1: Direct URL
        if req.url:
            if req.url.lower().endswith(".pdf") or "pdf" in req.url.lower():
                return await scrape_pdf(req.url)
            else:
                return await scrape_html(req.url)

        # Mode 2: Drug + payer → find policy URL → scrape it
        if req.drug_name and req.payer:
            return await find_and_scrape(req.drug_name, req.payer)

        return {"error": "Provide a url OR both drug_name and payer", "raw_text": None}

    except Exception as e:
        return {"error": str(e), "raw_text": None}


async def find_and_scrape(drug_name: str, payer: str):
    """Step 1: Find the policy URL. Step 2: Scrape it."""
    payer_key = payer.lower().strip()

    # Match payer to known key
    matched_key = None
    for key in PAYER_POLICY_URLS:
        if key in payer_key or payer_key in key:
            matched_key = key
            break

    if not matched_key:
        return {
            "error": f"Payer '{payer}' not supported yet.",
            "supported_payers": ["uhc", "united", "cigna", "bcbs", "bluecross", "emblemhealth", "aetna"],
            "raw_text": None
        }

    # Step 1: Use DuckDuckGo to find the specific policy page
    policy_url = await find_policy_url_duckduckgo(drug_name, payer_key)

    # Step 2: Fallback to payer index page if no specific URL found
    if not policy_url:
        policy_url = PAYER_POLICY_URLS[matched_key]

    # Step 3: Scrape whatever URL we found
    if policy_url.endswith(".pdf"):
        result = await scrape_pdf(policy_url)
    else:
        result = await scrape_html(policy_url)

    result["drug_name"] = drug_name
    result["payer"] = payer
    result["policy_url_found"] = policy_url

    return result


async def find_policy_url_duckduckgo(drug_name: str, payer: str) -> Optional[str]:
    """Search DuckDuckGo for the specific policy page URL"""
    try:
        payer_domains = {
            "uhc": "uhcprovider.com",
            "united": "uhcprovider.com",
            "cigna": "cigna.com",
            "bcbs": "bcbsnc.com",
            "bluecross": "bcbsnc.com",
            "aetna": "aetna.com",
            "emblemhealth": "emblemhealth.com",
        }

        domain = payer_domains.get(payer, "")
        query = f"{drug_name} medical benefit drug policy {payer} site:{domain}"

        # DuckDuckGo HTML search (no API key needed)
        search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"

        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        ) as client:
            response = await client.get(search_url, follow_redirects=True)
            html = response.text

        # Extract first result URL from DuckDuckGo HTML
        import re
        # DuckDuckGo result links look like: href="https://..."
        urls = re.findall(r'href="(https://[^"]+)"', html)

        # Filter to only URLs from the payer's domain
        for url in urls:
            if domain and domain in url:
                # Skip search pages, prefer policy/coverage pages
                if any(word in url.lower() for word in ["polic", "coverage", "drug", "medical", "benefit"]):
                    return url

        # Return first domain match even if not keyword filtered
        for url in urls:
            if domain and domain in url:
                return url

        return None

    except Exception as e:
        print(f"DuckDuckGo search failed: {e}")
        return None


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
