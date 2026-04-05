from fastapi import FastAPI
from pydantic import BaseModel
import subprocess
import sys
import httpx
import io
import re
from typing import Optional

app = FastAPI()

# These are verified working policy index URLs per payer
PAYER_POLICY_INDEXES = {
    "uhc": [
        "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html",
    ],
    "united": [
        "https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html",
    ],
    "cigna": [
        "https://www.cigna.com/healthcare-professionals/resources-for-health-care-professionals/clinical-payment-and-reimbursement-policies/medical-coverage-policies",
    ],
    "bcbs": [
        "https://www.bcbsnc.com/content/providers/clinical-policy-bulletins/index.htm",
    ],
    "bluecross": [
        "https://www.bcbsnc.com/content/providers/clinical-policy-bulletins/index.htm",
    ],
    "aetna": [
        "https://www.aetna.com/health-care-professionals/clinical-policy-bulletins/medical-clinical-policy-bulletins.html",
    ],
    "emblemhealth": [
        "https://www.emblemhealth.com/providers/clinical-resources/medical-policies",
    ],
}

PAYER_DOMAINS = {
    "uhc": "uhcprovider.com",
    "united": "uhcprovider.com",
    "cigna": "cigna.com",
    "bcbs": "bcbsnc.com",
    "bluecross": "bcbsnc.com",
    "aetna": "aetna.com",
    "emblemhealth": "emblemhealth.com",
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

        # Mode 2: Drug + payer
        if req.drug_name and req.payer:
            return await find_and_scrape(req.drug_name, req.payer)

        return {"error": "Provide a url OR both drug_name and payer", "raw_text": None}

    except Exception as e:
        return {"error": str(e), "raw_text": None}


async def find_and_scrape(drug_name: str, payer: str):
    payer_key = payer.lower().strip()

    # Match to known payer
    matched_key = None
    for key in PAYER_POLICY_INDEXES:
        if key in payer_key or payer_key in key:
            matched_key = key
            break

    if not matched_key:
        return {
            "error": f"Payer '{payer}' not supported.",
            "supported_payers": list(PAYER_POLICY_INDEXES.keys()),
            "raw_text": None
        }

    domain = PAYER_DOMAINS.get(matched_key, "")

    # Step 1: Try DuckDuckGo to find specific drug policy page
    print(f"Searching for {drug_name} policy at {payer}...")
    policy_url = await find_policy_url_duckduckgo(drug_name, matched_key, domain)

    # Step 2: Validate the URL actually exists
    if policy_url:
        is_valid = await check_url_exists(policy_url)
        if not is_valid:
            print(f"Found URL is 404, falling back to index: {policy_url}")
            policy_url = None

    # Step 3: Fallback to payer index page
    if not policy_url:
        policy_url = PAYER_POLICY_INDEXES[matched_key][0]
        print(f"Using payer index page: {policy_url}")

    # Step 4: Scrape
    if ".pdf" in policy_url.lower():
        result = await scrape_pdf(policy_url)
    else:
        result = await scrape_html(policy_url)

    result["drug_name"] = drug_name
    result["payer"] = payer
    result["policy_url_found"] = policy_url

    return result


async def check_url_exists(url: str) -> bool:
    """Check if URL returns 200 before scraping it"""
    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        ) as client:
            response = await client.head(url, follow_redirects=True)
            return response.status_code == 200
    except:
        return False


async def find_policy_url_duckduckgo(drug_name: str, payer: str, domain: str) -> Optional[str]:
    try:
        query = f"{drug_name} medical benefit drug policy {payer} site:{domain}"
        search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"

        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        ) as client:
            response = await client.get(search_url, follow_redirects=True)
            html = response.text

        urls = re.findall(r'href="(https://[^"]+)"', html)

        # Prefer PDF policy pages
        for url in urls:
            if domain in url and url.endswith(".pdf"):
                if any(w in url.lower() for w in ["polic", "coverage", "drug", "medical"]):
                    return url

        # Then try HTML policy pages
        for url in urls:
            if domain in url:
                if any(w in url.lower() for w in ["polic", "coverage", "drug", "medical", "benefit"]):
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
