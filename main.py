from fastapi import FastAPI
from pydantic import BaseModel
import subprocess
import sys
import httpx
import io
import re
from typing import Optional

app = FastAPI()

PAYER_POLICY_INDEXES = {
    # UnitedHealthcare
    "uhc": ["https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html"],
    "united": ["https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html"],
    "unitedhealthcare": ["https://www.uhcprovider.com/en/policies-protocols/commercial-policies/commercial-medical-drug-policies.html"],

    # Cigna
    "cigna": ["https://static.cigna.com/assets/chcp/pdf/coveragePolicies/medical/ad_a004_administrativepolicy_preventive_care_services.pdf"],

    # EmblemHealth
    "emblemhealth": ["https://www.emblemhealth.com/providers/clinical-resources/medical-policies"],
    "emblem": ["https://www.emblemhealth.com/providers/clinical-resources/medical-policies"],

    # UPMC Health Plan
    "upmc": ["https://www.upmchealthplan.com/providers/medicalpolicies/priorauth.aspx"],
    "upmc health plan": ["https://www.upmchealthplan.com/providers/medicalpolicies/priorauth.aspx"],

    # Priority Health
    "priority health": ["https://www.priorityhealth.com/provider/medical-drug-list"],
    "priorityhealth": ["https://www.priorityhealth.com/provider/medical-drug-list"],

    # Blue Cross Blue Shield of North Carolina
    "bcbs": ["https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial-medical-policy-updates"],
    "bluecross": ["https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial-medical-policy-updates"],
    "blue cross": ["https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial-medical-policy-updates"],
    "bcbs nc": ["https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial-medical-policy-updates"],
    "blue cross blue shield": ["https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial-medical-policy-updates"],

    # Florida Blue
    "florida blue": ["https://www.floridablue.com/providers/tools-and-resources/clinical-guidelines-and-coverage-policies"],
    "floridablue": ["https://www.floridablue.com/providers/tools-and-resources/clinical-guidelines-and-coverage-policies"],

    # Aetna
    "aetna": ["https://www.aetna.com/health-care-professionals/clinical-policy-bulletins/medical-clinical-policy-bulletins.html"],
}

PAYER_DOMAINS = {
    "uhc": "uhcprovider.com",
    "united": "uhcprovider.com",
    "unitedhealthcare": "uhcprovider.com",
    "cigna": "static.cigna.com",
    "emblemhealth": "emblemhealth.com",
    "emblem": "emblemhealth.com",
    "upmc": "upmchealthplan.com",
    "upmc health plan": "upmchealthplan.com",
    "priority health": "priorityhealth.com",
    "priorityhealth": "priorityhealth.com",
    "bcbs": "bluecrossnc.com",
    "bluecross": "bluecrossnc.com",
    "blue cross": "bluecrossnc.com",
    "bcbs nc": "bluecrossnc.com",
    "blue cross blue shield": "bluecrossnc.com",
    "florida blue": "floridablue.com",
    "floridablue": "floridablue.com",
    "aetna": "aetna.com",
}

# Payers where DuckDuckGo returns bad results — go straight to index
SKIP_SEARCH_PAYERS = [
    "cigna",
    "emblemhealth",
    "emblem",
    "priority health",
    "priorityhealth",
    "florida blue",
    "floridablue",
    "upmc",
    "upmc health plan",
]

# BCBS name variants
BCBS_KEYS = ["bcbs", "bluecross", "blue cross", "bcbs nc", "blue cross blue shield"]


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

    # Match to known payer — check exact match first, then partial
    matched_key = None

    # Exact match first
    if payer_key in PAYER_POLICY_INDEXES:
        matched_key = payer_key

    # Partial match second
    if not matched_key:
        for key in PAYER_POLICY_INDEXES:
            if key in payer_key or payer_key in key:
                matched_key = key
                break

    if not matched_key:
        return {
            "error": f"Payer '{payer}' not supported.",
            "supported_payers": [
                "UnitedHealthcare", "Cigna", "EmblemHealth",
                "UPMC Health Plan", "Priority Health",
                "Blue Cross Blue Shield (BCBS NC)",
                "Florida Blue", "Aetna"
            ],
            "raw_text": None
        }

    domain = PAYER_DOMAINS.get(matched_key, "")
    policy_url = None

    # BCBS — use dedicated URL pattern handler
    if matched_key in BCBS_KEYS:
        print(f"Using BCBS-specific search for {drug_name}...")
        policy_url = await find_bcbs_policy(drug_name)

    # Payers where DuckDuckGo doesn't work — go straight to index
    elif matched_key in SKIP_SEARCH_PAYERS:
        print(f"Skipping DuckDuckGo for {matched_key} — going straight to index")

    # All others — try DuckDuckGo first
    else:
        print(f"Searching DuckDuckGo for {drug_name} at {payer}...")
        policy_url = await find_policy_url_duckduckgo(drug_name, matched_key, domain)

        if policy_url:
            is_valid = await check_url_valid(policy_url)
            if not is_valid:
                print(f"URL invalid or 404 content, falling back: {policy_url}")
                policy_url = None

    # Fallback to known index page
    if not policy_url:
        policy_url = PAYER_POLICY_INDEXES[matched_key][0]
        print(f"Using payer index page: {policy_url}")

    # Scrape
    if ".pdf" in policy_url.lower():
        result = await scrape_pdf(policy_url)
    else:
        result = await scrape_html(policy_url)

    # Check if scraped content is actually a 404 page
    raw = result.get("raw_text", "") or ""
    if raw and ("404" in raw[:300] or "not found" in raw[:300].lower()):
        print(f"Got 404 page content, retrying with index page")
        fallback_url = PAYER_POLICY_INDEXES[matched_key][0]
        result = await scrape_html(fallback_url)
        policy_url = fallback_url

    result["drug_name"] = drug_name
    result["payer"] = payer
    result["policy_url_found"] = policy_url

    return result


async def find_bcbs_policy(drug_name: str) -> Optional[str]:
    """
    BCBS NC URL pattern:
    bluecrossnc.com/providers/policies-guidelines-codes/
      commercial/[category]/updates/[drug-slug]
    Try known categories, then fallback to DuckDuckGo.
    """
    try:
        drug_slug = drug_name.lower().replace(" ", "-")

        candidate_urls = [
            f"https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial/pharmacy/updates/{drug_slug}",
            f"https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial/oncology/updates/{drug_slug}",
            f"https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial/medical/updates/{drug_slug}",
            f"https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial/administrative/updates/{drug_slug}",
            f"https://www.bluecrossnc.com/providers/policies-guidelines-codes/commercial/drug/updates/{drug_slug}",
            f"https://www.bluecrossnc.com/providers/policies-guidelines-codes/medicare/updates/{drug_slug}",
        ]

        for url in candidate_urls:
            print(f"Trying BCBS URL: {url}")
            is_valid = await check_url_valid(url)
            if is_valid:
                print(f"Found valid BCBS URL: {url}")
                return url

        # Fallback: DuckDuckGo scoped to bluecrossnc.com
        print(f"No direct BCBS URL found, trying DuckDuckGo...")
        query = f"{drug_name} medical policy site:bluecrossnc.com providers policies-guidelines-codes"
        search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"

        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        ) as client:
            response = await client.get(search_url, follow_redirects=True)
            html = response.text

        urls = re.findall(r'href="(https://[^"]+)"', html)

        for url in urls:
            if "bluecrossnc.com/providers/policies-guidelines-codes" in url:
                if any(w in url.lower() for w in ["updates", "policy", "drug", "commercial"]):
                    is_valid = await check_url_valid(url)
                    if is_valid:
                        print(f"Found BCBS via DuckDuckGo: {url}")
                        return url

        return None

    except Exception as e:
        print(f"BCBS search failed: {e}")
        return None


async def check_url_valid(url: str) -> bool:
    """Check URL returns 200 and is not a soft 404"""
    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        ) as client:
            response = await client.get(url, follow_redirects=True)

            if response.status_code != 200:
                return False

            content_preview = response.text[:500].lower()
            if "404" in content_preview or "page not found" in content_preview or "not found" in content_preview:
                return False

            return True
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

        # Prefer PDF policy pages first
        for url in urls:
            if domain in url and url.endswith(".pdf"):
                if any(w in url.lower() for w in ["polic", "coverage", "drug", "medical"]):
                    return url

        # Then HTML policy pages
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
