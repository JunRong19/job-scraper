import requests
import time
import random
import logging
import json
import re
import xml.etree.ElementTree as ET
import config
import supabase_utils
from scraper import convert_html_to_markdown

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# JobStreet and Careers@Gov both get blocked (403) from GitHub-hosted runner IPs — this
# script is meant to run on a self-hosted runner instead. JobStreet's bot protection also
# rejects the stale (circa-2015) user agents in user_agents.py, so a small pool of current
# desktop browser UAs is used for both sites here.
MODERN_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# =====================================================================
# JobStreet Scraping Logic
# =====================================================================
def _extract_seek_redux_data(html: str) -> dict | None:
    """
    Extracts the `window.SEEK_REDUX_DATA = {...}` JSON blob embedded in JobStreet's
    server-rendered HTML. Uses raw_decode instead of a regex so the parse is exact
    regardless of what characters appear afterwards in the page.
    """
    marker = "window.SEEK_REDUX_DATA = "
    idx = html.find(marker)
    if idx == -1:
        return None

    try:
        obj, _ = json.JSONDecoder().raw_decode(html, idx + len(marker))
        return obj
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse SEEK_REDUX_DATA JSON: {e}")
        return None

def _fetch_jobstreet_job_ids(search_query: str) -> list:
    """Fetches job IDs from JobStreet search results pages with delays, rotating user agents, and retries."""

    job_ids_list = []
    page = 1
    max_pages = config.JOBSTREET_MAX_PAGES
    total_count = None

    logging.info(f"--- Starting Phase 1: Scraping JobStreet Job IDs (Max Pages: {max_pages}) ---")
    while page <= max_pages:
        target_url = (
            f"{config.JOBSTREET_BASE_URL}/jobs?keywords={search_query.replace(' ', '%20')}"
            f"&daterange={config.JOBSTREET_DATE_RANGE}&sortmode={config.JOBSTREET_SORT_MODE}&page={page}"
        )

        if page > 1:
            sleep_time = random.uniform(5.0, 15.0)
            logging.info(f"Waiting for {sleep_time:.2f} seconds before next request...")
            time.sleep(sleep_time)

        user_agent = random.choice(MODERN_USER_AGENTS)
        headers = {'User-Agent': user_agent}

        logging.info(f"Scraping URL: {target_url}")

        res = None
        retries = 0
        while retries <= config.MAX_RETRIES:
            try:
                res = requests.get(target_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
                res.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                    retries += 1
                    wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5)

                    logging.warning(f"Error 429: Too Many Requests. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                    time.sleep(wait_time)

                    user_agent = random.choice(MODERN_USER_AGENTS)
                    headers = {'User-Agent': user_agent}

                    logging.info(f"Retrying with new User-Agent: {user_agent}")
                    continue
                else:
                    logging.error(f"HTTP Error fetching JobStreet search results page: {e}")
                    res = None
                    break
            except requests.exceptions.RequestException as e:
                logging.error(f"Request Exception fetching JobStreet search results page: {e}")
                res = None
                break

        if res is None:
            logging.error(f"Failed to fetch {target_url} after {retries} retries. Stopping pagination for this query.")
            break

        redux_data = _extract_seek_redux_data(res.text)
        if not redux_data:
            logging.warning(f"Could not find/parse SEEK_REDUX_DATA at page={page}, stopping.")
            break

        results = redux_data.get('results', {}) or {}
        page_job_ids = results.get('jobIds') or []

        if total_count is None:
            total_count = results.get('totalCount')
            logging.info(f"JobStreet reports total potential jobs matching criteria: {total_count}")

        new_ids = [jid for jid in page_job_ids if jid not in job_ids_list]
        job_ids_list.extend(new_ids)
        logging.info(f"Added {len(new_ids)} unique job IDs from page {page}. Total so far: {len(job_ids_list)}.")

        if not page_job_ids or (total_count is not None and len(job_ids_list) >= total_count):
            logging.info("No more JobStreet job pages to fetch.")
            break

        page += 1

    logging.info(f"--- Finished Phase 1: Found {len(job_ids_list)} unique job IDs during scraping ---")
    return job_ids_list

def _fetch_jobstreet_job_details(job_id: str) -> dict | None:
    """Fetches detailed information for a single job ID with delays, rotating user agents, and retries."""

    job_detail_url = f"{config.JOBSTREET_BASE_URL}/job/{job_id}"

    logging.info(f"Preparing to fetch details for JobStreet job ID: {job_id}")

    sleep_time = random.uniform(3.0, 10.0)
    logging.info(f"Waiting for {sleep_time:.2f} seconds before fetching details...")
    time.sleep(sleep_time)

    user_agent = random.choice(MODERN_USER_AGENTS)
    headers = {'User-Agent': user_agent}

    logging.info(f"Fetching details from: {job_detail_url}")

    resp = None
    retries = 0
    while retries <= config.MAX_RETRIES:
        try:
            resp = requests.get(job_detail_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                retries += 1
                wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5)

                logging.warning(f"Error 429 for job ID {job_id}. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                user_agent = random.choice(MODERN_USER_AGENTS)
                headers = {'User-Agent': user_agent}

                logging.info(f"Retrying job {job_id} with new User-Agent: {user_agent}")
                continue
            elif e.response.status_code == 404:
                logging.warning(f"Job details not found (404) for JobStreet job ID: {job_id}.")
                return None
            else:
                logging.error(f"HTTP Error fetching details for JobStreet job ID {job_id}: {e}")
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Request Exception fetching details for JobStreet job ID {job_id}: {e}")
            return None

    if resp is None:
        logging.error(f"Failed to fetch details for JobStreet job ID {job_id} after {retries} retries (unexpected state).")
        return None

    try:
        redux_data = _extract_seek_redux_data(resp.text)
        if not redux_data:
            logging.warning(f"Could not find/parse SEEK_REDUX_DATA for JobStreet job ID {job_id}.")
            return None

        job = (redux_data.get('jobdetails', {}) or {}).get('result', {}).get('job')
        if not job:
            logging.warning(f"No job data found in SEEK_REDUX_DATA for JobStreet job ID {job_id}.")
            return None

        if job.get('isExpired'):
            logging.info(f"Skipping JobStreet job ID {job_id}: listing is expired.")
            return None

        advertiser = job.get('advertiser') or {}
        location_info = job.get('location') or {}
        listed_at = job.get('listedAt') or {}

        content_html = job.get('content') or ''
        description = convert_html_to_markdown(content_html) if content_html.strip() else None
        if not content_html.strip():
            logging.warning(f"Description HTML was empty for JobStreet job ID {job_id}. Skipping conversion.")

        job_details = {
            "job_id": str(job.get('id') or job_id),
            "company": advertiser.get('name'),
            "job_title": job.get('title'),
            "location": location_info.get('label'),
            "level": None,
            "provider": "jobstreet",
            "description": description,
            "posted_at": listed_at.get('dateTimeUtc'),
        }

        return job_details

    except Exception as e:
        logging.error(f"General Error processing details for JobStreet job ID {job_id} after successful fetch: {e}")
        return None

def process_jobstreet_query(search_query: str, limit: int = None) -> list:
    """
    Orchestrates scraping and detail fetching for a single JobStreet query,
    filtering against existing jobs in Supabase BEFORE fetching details.
    Returns a list of new job details found.
    """

    scraped_job_ids = _fetch_jobstreet_job_ids(search_query)
    if not scraped_job_ids:
        logging.info("No job IDs found in Phase 1. Skipping detail fetching.")
        return []

    unique_jobstreet_job_ids = list(set(scraped_job_ids))

    logging.info(f"Found {len(scraped_job_ids)} raw job IDs, {len(unique_jobstreet_job_ids)} unique IDs after scraping.")

    logging.info("\n--- Starting Filtering Step: Checking against Supabase ---")
    job_ids_set, company_title_set = supabase_utils.get_existing_jobs_from_supabase()

    new_job_ids_to_process = [
        str(job_id) for job_id in unique_jobstreet_job_ids
        if str(job_id) not in job_ids_set
    ]

    logging.info(f"Found {len(unique_jobstreet_job_ids)} unique scraped IDs.")
    logging.info(f"Found {len(job_ids_set)} existing IDs in Supabase.")
    logging.info(f"Identified {len(new_job_ids_to_process)} new job IDs to fetch details for.")

    if not new_job_ids_to_process:
        logging.info("No new job IDs to process after filtering.")
        return []

    if limit is not None and len(new_job_ids_to_process) > limit:
        logging.info(f"Truncating new_job_ids_to_process from {len(new_job_ids_to_process)} to {limit} to stay within source limit.")
        new_job_ids_to_process = new_job_ids_to_process[:limit]

    logging.info(f"\n--- Starting Phase 2: Fetching Job Details for {len(new_job_ids_to_process)} New IDs ---")
    detailed_new_jobs = []
    processed_count = 0

    for job_id in new_job_ids_to_process:
        details = _fetch_jobstreet_job_details(job_id)
        if details:
            description = details.get('description')
            if description and description.strip():
                if 'job_id' in details and details['job_id'] is not None:
                    detailed_new_jobs.append(details)
                    processed_count += 1
                else:
                    logging.warning(f"Fetched details for {job_id} but missing 'job_id' key. Skipping.")
            else:
                logging.warning(f"Skipping job ID {job_id} due to missing or empty description.")
        else:
            logging.warning(f"Skipping job ID {job_id} as detail fetching failed or returned no data.")

    logging.info(f"--- Finished Phase 2: Successfully fetched details for {processed_count} new job(s) ---")
    return detailed_new_jobs

# =====================================================================
# Careers@Gov Scraping Logic
# =====================================================================
def _parse_careers_gov_sitemap(xml_text: str) -> list:
    """Parses sitemap.xml into a list of (job_id, uuid) tuples from /jobs/hrp/{id}/{uuid} URLs."""
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logging.error(f"Failed to parse Careers@Gov sitemap XML: {e}")
        return entries

    for url_el in root.findall("sm:url", ns):
        loc_el = url_el.find("sm:loc", ns)
        if loc_el is None or not loc_el.text:
            continue
        m = re.search(r"/jobs/hrp/(\d+)/([0-9a-fA-F-]+)", loc_el.text)
        if m:
            entries.append((m.group(1), m.group(2)))

    return entries

def _extract_job_posting_ld_json(html: str) -> dict | None:
    """Extracts the schema.org JobPosting JSON-LD block Careers@Gov embeds in every job page."""
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None

def _matches_careers_gov_keywords(title: str | None) -> bool:
    if not title:
        return False
    title_lower = title.lower()
    return any(keyword.lower() in title_lower for keyword in config.CAREERS_GOV_SEARCH_QUERIES)

def _fetch_careers_gov_job_details(job_id: str, uuid: str) -> dict | None:
    """Fetches a Careers@Gov job page and extracts its JobPosting JSON-LD, filtered by title keywords."""

    job_detail_url = f"{config.CAREERS_GOV_BASE_URL}/jobs/hrp/{job_id}/{uuid}"

    logging.info(f"Preparing to fetch details for Careers@Gov job ID: {job_id}")

    sleep_time = random.uniform(3.0, 10.0)
    logging.info(f"Waiting for {sleep_time:.2f} seconds before fetching details...")
    time.sleep(sleep_time)

    user_agent = random.choice(MODERN_USER_AGENTS)
    headers = {'User-Agent': user_agent}

    logging.info(f"Fetching details from: {job_detail_url}")

    resp = None
    retries = 0
    while retries <= config.MAX_RETRIES:
        try:
            resp = requests.get(job_detail_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                retries += 1
                wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5)

                logging.warning(f"Error 429 for job ID {job_id}. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                user_agent = random.choice(MODERN_USER_AGENTS)
                headers = {'User-Agent': user_agent}

                logging.info(f"Retrying job {job_id} with new User-Agent: {user_agent}")
                continue
            elif e.response.status_code == 404:
                logging.warning(f"Job details not found (404) for Careers@Gov job ID: {job_id}.")
                return None
            else:
                logging.error(f"HTTP Error fetching details for Careers@Gov job ID {job_id}: {e}")
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Request Exception fetching details for Careers@Gov job ID {job_id}: {e}")
            return None

    if resp is None:
        logging.error(f"Failed to fetch details for Careers@Gov job ID {job_id} after {retries} retries (unexpected state).")
        return None

    try:
        posting = _extract_job_posting_ld_json(resp.text)
        if not posting:
            logging.warning(f"No JobPosting JSON-LD found for Careers@Gov job ID {job_id}.")
            return None

        title = posting.get("title")
        if not _matches_careers_gov_keywords(title):
            logging.info(f"Skipping Careers@Gov job ID {job_id}: title '{title}' doesn't match search keywords.")
            return None

        hiring_org = posting.get("hiringOrganization") or {}
        content_html = posting.get("description") or ""
        description = convert_html_to_markdown(content_html) if content_html.strip() else None
        if not content_html.strip():
            logging.warning(f"Description HTML was empty for Careers@Gov job ID {job_id}. Skipping conversion.")

        job_details = {
            "job_id": job_id,
            "company": hiring_org.get("name"),
            "job_title": title,
            "location": "Singapore",
            "level": None,
            "provider": "careers_gov",
            "description": description,
            "posted_at": posting.get("datePosted"),
        }

        return job_details

    except Exception as e:
        logging.error(f"General Error processing details for Careers@Gov job ID {job_id} after successful fetch: {e}")
        return None

def process_careers_gov() -> list:
    """
    Orchestrates scraping for Careers@Gov: fetches the full sitemap (the only crawl path
    robots.txt allows besides /api/, which is explicitly disallowed), filters against
    Supabase, then fetches + keyword-filters details for the newest unseen candidates.
    """

    logging.info("--- Starting Phase 1: Fetching Careers@Gov sitemap ---")
    sitemap_url = f"{config.CAREERS_GOV_BASE_URL}/sitemap.xml"

    user_agent = random.choice(MODERN_USER_AGENTS)
    headers = {'User-Agent': user_agent}

    try:
        res = requests.get(sitemap_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
        res.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch Careers@Gov sitemap: {e}")
        return []

    entries = _parse_careers_gov_sitemap(res.text)
    logging.info(f"--- Finished Phase 1: Sitemap contains {len(entries)} job URLs ---")
    if not entries:
        return []

    logging.info("\n--- Starting Filtering Step: Checking against Supabase ---")
    job_ids_set, _ = supabase_utils.get_existing_jobs_from_supabase()

    candidates = [(jid, uuid) for jid, uuid in entries if jid not in job_ids_set]
    logging.info(f"Found {len(entries)} sitemap entries, {len(job_ids_set)} existing IDs in Supabase, {len(candidates)} new candidates.")

    if not candidates:
        logging.info("No new candidate job IDs to process after filtering.")
        return []

    limit = config.CAREERS_GOV_MAX_NEW_JOBS_PER_RUN
    if limit is not None and len(candidates) > limit:
        logging.info(f"Truncating candidates from {len(candidates)} to {limit} to bound this run.")
        candidates = candidates[:limit]

    logging.info(f"\n--- Starting Phase 2: Fetching + Filtering {len(candidates)} Candidate Job(s) ---")
    detailed_new_jobs = []
    processed_count = 0

    for job_id, uuid in candidates:
        details = _fetch_careers_gov_job_details(job_id, uuid)
        if details:
            description = details.get('description')
            if description and description.strip():
                detailed_new_jobs.append(details)
                processed_count += 1
            else:
                logging.warning(f"Skipping job ID {job_id} due to missing or empty description.")
        # No else-log here: _fetch_careers_gov_job_details already logs the reason
        # (fetch failure, no JSON-LD, or keyword mismatch) for the None case.

    logging.info(f"--- Finished Phase 2: Successfully fetched details for {processed_count} new job(s) ---")
    return detailed_new_jobs

# --- Main Execution ---
if __name__ == "__main__":

    total_new_jobs_saved = 0

    if "careers_gov" in config.SELF_HOSTED_SCRAPING_SOURCES:
        logging.info("\n--- Starting Careers@Gov Job Scraping ---")
        new_careers_gov_jobs = process_careers_gov()
        if new_careers_gov_jobs:
            print(f"\n--- Saving {len(new_careers_gov_jobs)} new Careers@Gov job(s) ---")
            supabase_utils.save_jobs_to_supabase(new_careers_gov_jobs)
            total_new_jobs_saved += len(new_careers_gov_jobs)
        else:
            print("\nNo new Careers@Gov job details were fetched or processed.")
    else:
        logging.info("\n--- Skipping Careers@Gov Job Scraping per config ---")

    if "jobstreet" in config.SELF_HOSTED_SCRAPING_SOURCES:
        logging.info("\n--- Starting JobStreet Job Scraping ---")
        max_jobs_per_search = config.MAX_JOBS_PER_SEARCH.get("jobstreet", getattr(config, 'DEFAULT_MAX_JOBS_PER_SEARCH', 10))
        for query in config.JOBSTREET_SEARCH_QUERIES:
            print(f"\n{'='*20} Processing JobStreet Search Query: '{query}' {'='*20}")

            new_jobstreet_job_details = process_jobstreet_query(query, limit=max_jobs_per_search)

            if new_jobstreet_job_details:
                print(f"\n--- Saving {len(new_jobstreet_job_details)} new job(s) for query '{query}' ---")
                supabase_utils.save_jobs_to_supabase(new_jobstreet_job_details)
                total_new_jobs_saved += len(new_jobstreet_job_details)
            else:
                print(f"\nNo new job details were fetched or processed for query '{query}'.")
    else:
        logging.info("\n--- Skipping JobStreet Job Scraping per config ---")

    # --- End of Script ---
    logging.info(f"\n{'='*20} Self-hosted scraping script finished {'='*20}")
    logging.info(f"Total new jobs saved: {total_new_jobs_saved}")
