import os
from dotenv import load_dotenv

load_dotenv()

# --- DO NOT MODIFY THE BELOW SECTION ---

# =================================================================
# 1. CORE SYSTEM CONFIGURATION (Do Not Modify)
# =================================================================
SUPABASE_URL: str = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_TABLE_NAME: str = "jobs"
SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME = "customized_resumes"
SUPABASE_STORAGE_BUCKET="personalized_resumes"
SUPABASE_RESUME_STORAGE_BUCKET="resumes"
SUPABASE_BASE_RESUME_TABLE_NAME = "base_resume"
BASE_RESUME_PATH = "resume.json"

# API keys — set only the key(s) needed for your chosen provider.
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_FIRST_API_KEY")

# =================================================================
# 2. USER PREFERENCES (Editable)
# =================================================================

# --- LLM Settings ---
# Use any model supported by LiteLLM (gemini, openai/gpt-4o-mini, groq/llama-3.3-70b-versatile)
# Full list of supported models & naming: https://docs.litellm.ai/docs/providers
LLM_MODEL = "openai/gpt-5.4-nano"

# --- Search Configuration ---
LINKEDIN_SEARCH_QUERIES = ["software", "ai", "unity"]
LINKEDIN_LOCATION = "Singapore"
LINKEDIN_GEO_ID = 102454443      # Singapore: 102454443, Dubai: 100205264
LINKEDIN_JOB_TYPE = "F" # F=Full-time, C=Contract, P=Part-time, T=Temporary, I=Internship
LINKEDIN_JOB_POSTING_DATE = "r86400" # r86400=Past 24h, r604800=Past week
LINKEDIN_F_WT = 1 # 1=Onsite, 2=Remote, 3=Hybrid
# 1=Internship, 2=Entry level, 3=Associate, 4=Mid-Senior level, 5=Director, 6=Executive.
# NOTE: verified via live test that LinkedIn's public/guest search API (no login) only
# reliably enforces f_E=1 (Internship) — f_E=2 (Entry level) returned byte-identical
# results to no f_E filter and to f_E=4 (Mid-Senior) in side-by-side testing. Sent anyway
# since it's the technically-correct param and doesn't hurt, but don't rely on it actually
# narrowing results on this endpoint.
LINKEDIN_EXPERIENCE_LEVEL = 2

CAREERS_FUTURE_SEARCH_QUERIES = ["software", "ai", "unity"]
CAREERS_FUTURE_SEARCH_CATEGORIES = ["Information Technology"]
CAREERS_FUTURE_SEARCH_EMPLOYMENT_TYPES = ["Full Time"]
# Verified via live API test: 546 -> 9 results, every result correctly tagged "Fresh/entry level".
CAREERS_FUTURE_POSITION_LEVELS = ["Fresh/entry level"]

# --- JobStreet & Careers@Gov (scraped by selfhosted_scraper.py on a self-hosted runner —
# both sites block GitHub-hosted runner IPs at the WAF level) ---
JOBSTREET_SEARCH_QUERIES = ["software", "ai", "unity"]
JOBSTREET_BASE_URL = "https://sg.jobstreet.com"
JOBSTREET_DATE_RANGE = 1 # Days since posting: 1=Past 24h, 3=Last 3 days, 7, 14, 30
JOBSTREET_SORT_MODE = "ListedDate" # "ListedDate" (newest first) or "KeywordRelevance"
JOBSTREET_MAX_PAGES = 1 # 30 job IDs per page
# Verified live via SSR state echo (window.SEEK_REDUX_DATA): both params round-trip
# correctly as plain query params on the generic /jobs?keywords=... path, no need for
# JobStreet's SEO slug URLs (e.g. /software-engineer-jobs/full-time/on-site).
# workarrangement dropped: stacking full-time + on-site + tight date range returned
# almost no hits — most SG listings don't set on-site explicitly.
JOBSTREET_WORK_TYPE = "242" # Full time (SEEK internal code; confirmed via /software-engineer-jobs/full-time)

CAREERS_GOV_BASE_URL = "https://jobs.careers.gov.sg"
# Careers@Gov's own search (used by the browser search bar) hits Algolia directly, not
# jobs.careers.gov.sg/api/ — a separate third-party domain the site explicitly calls
# client-side with a public, referer-restricted, search-only API key. Confirmed via
# browser network inspection. This is unrelated to robots.txt's Disallow: /api/, which
# only scopes jobs.careers.gov.sg's own domain.
CAREERS_GOV_ALGOLIA_URL = "https://3ow7d8b4iz-dsn.algolia.net/1/indexes/job_index/query"
CAREERS_GOV_ALGOLIA_APP_ID = "3OW7D8B4IZ"
CAREERS_GOV_ALGOLIA_API_KEY = "32fa71d8b0bc06be1e6395bf8c430107"
CAREERS_GOV_SEARCH_QUERIES = ["software", "ai", "unity"]
# The public Algolia key above has `filters`/`facetFilters` completely disabled — any use
# of either param (even referencing a nonexistent field) silently returns nbHits=0, no
# error. Confirmed real employment-type/experience-level filtering happens entirely
# client-side in the browser, against a ~2000-job dataset embedded as JSON on every page
# load (reverse-engineered from the site's own JS bundle, chunks/app/page-*.js). We fetch
# that same dataset once per run and cross-reference Algolia's search hits against it.
# "Full-time" in the site's filter UI maps to employmentType being any of these three
# (case-insensitive) — verified this exact mapping against the live UI's result count.
CAREERS_GOV_FULL_TIME_EMPLOYMENT_TYPES = {"full-time", "permanent", "permanent/contract"}
CAREERS_GOV_EXPERIENCE_LEVEL = "0 - 1 year"

SELF_HOSTED_SCRAPING_SOURCES = ["careers_gov", "jobstreet"] # "careers_gov", "jobstreet"

# --- Processing Limits ---
SCRAPING_SOURCES = ["linkedin", "careers_future"] # "linkedin", "careers_future"
JOBS_TO_SCORE_PER_RUN = 5 # Batch size per fetch in score_jobs.py; the script loops until all eligible jobs are scored, not a per-run cap
JOBS_TO_CUSTOMIZE_PER_RUN = 1
MAX_JOBS_PER_SEARCH = {
    "linkedin": 15,
    "careers_future": 15,
    "jobstreet": 10,
    "careers_gov": 10,
}

# Jobs whose title contains any of these (case-insensitive substring) are skipped during
# scraping, across all sources, before being saved to Supabase.
EXCLUDED_JOB_TITLE_KEYWORDS = ["intern", "senior", "manager"]

# =================================================================
# 3. ADVANCED SYSTEM SETTINGS (Modify with Caution)
# =================================================================
LLM_MAX_RPM = 10
LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_DELAY = 10
LLM_DAILY_REQUEST_BUDGET = 0
LLM_REQUEST_DELAY_SECONDS = 8

LINKEDIN_MAX_START = 1 
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15

JOB_EXPIRY_DAYS = 30
JOB_CHECK_DAYS = 3
JOB_DELETION_DAYS = 60
JOB_CHECK_LIMIT = 50
ACTIVE_CHECK_TIMEOUT = 20
ACTIVE_CHECK_MAX_RETRIES = 2
ACTIVE_CHECK_RETRY_DELAY = 10
