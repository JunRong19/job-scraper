import requests
import time
import random
import logging
import json
import re
from datetime import datetime, timezone
import config
import supabase_utils
from scraper import convert_html_to_markdown, job_title_is_excluded

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
            f"&daterange={config.JOBSTREET_DATE_RANGE}&sortmode={config.JOBSTREET_SORT_MODE}"
            f"&worktype={config.JOBSTREET_WORK_TYPE}&workarrangement={config.JOBSTREET_WORK_ARRANGEMENT}&page={page}"
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
            if job_title_is_excluded(details.get('job_title')):
                logging.info(f"Skipping job ID {job_id}: title '{details.get('job_title')}' matches an excluded keyword.")
                continue
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
def _find_jobs_array(obj):
    """Recursively searches a parsed Next.js RSC payload for the first dict with a 'jobs' list key."""
    if isinstance(obj, dict):
        if isinstance(obj.get('jobs'), list):
            return obj['jobs']
        for v in obj.values():
            found = _find_jobs_array(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_jobs_array(v)
            if found is not None:
                return found
    return None

def _fetch_careers_gov_job_dataset() -> dict:
    """
    Fetches the ~2000-job dataset Careers@Gov embeds as JSON on every page load, used to
    power the site's employment-type/experience-level filters entirely client-side.

    This exists because the public Algolia key used for search (see
    _fetch_careers_gov_search_hits) has `filters`/`facetFilters` completely disabled —
    verified live: any use of either param, even referencing a field that doesn't exist,
    silently returns nbHits=0 with no error. The real filtering happens in the browser
    against this embedded dataset instead (reverse-engineered from the site's own JS
    bundle, chunks/app/page-*.js — see CAREERS_GOV_FULL_TIME_EMPLOYMENT_TYPES).

    Returns a dict keyed by the job's "id" field, which matches an Algolia objectID with
    its "HRP:"/"GREENHOUSE:" source prefix stripped (see
    _careers_gov_job_id_from_object_id). Fetched once per script run, not once per query.
    """
    headers = {'User-Agent': random.choice(MODERN_USER_AGENTS)}

    try:
        resp = requests.get(config.CAREERS_GOV_BASE_URL + '/', headers=headers, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch Careers@Gov job dataset: {e}")
        return {}

    html = resp.text
    marker = '\\"jobs\\":['
    marker_idx = html.find(marker)
    if marker_idx == -1:
        logging.warning("Could not find embedded jobs dataset on Careers@Gov homepage.")
        return {}

    push_start = html.rfind('self.__next_f.push([', 0, marker_idx)
    push_end = html.find('])</script>', marker_idx)
    if push_start == -1 or push_end == -1:
        logging.warning("Could not locate RSC script boundaries around Careers@Gov jobs dataset.")
        return {}

    chunk = html[push_start + len('self.__next_f.push('):push_end + 1]
    try:
        outer = json.loads(chunk)
        inner = outer[1]
        colon_idx = inner.find(':')
        data = json.loads(inner[colon_idx + 1:])
    except (json.JSONDecodeError, IndexError, ValueError, TypeError) as e:
        logging.error(f"Failed to parse Careers@Gov jobs dataset RSC payload: {e}")
        return {}

    jobs_list = _find_jobs_array(data)
    if jobs_list is None:
        logging.warning("Parsed Careers@Gov RSC payload but found no 'jobs' array inside it.")
        return {}

    dataset = {job['id']: job for job in jobs_list if isinstance(job, dict) and job.get('id')}
    logging.info(f"Fetched Careers@Gov job dataset: {len(dataset)} jobs (for employment-type/experience-level filtering).")
    return dataset

def _careers_gov_job_id_from_object_id(object_id: str) -> str | None:
    """Strips the Algolia objectID's source prefix to match the job dataset's "id" field."""
    if object_id.startswith("HRP:"):
        return object_id[4:]
    if object_id.startswith("GREENHOUSE:"):
        return object_id[11:]
    return None

def _careers_gov_matches_filters(job_meta: dict | None) -> bool:
    """
    Checks a job dataset entry against CAREERS_GOV_FULL_TIME_EMPLOYMENT_TYPES /
    CAREERS_GOV_EXPERIENCE_LEVEL. The "Full-time" employment-type filter option on the
    live site maps to more than a literal "Full-time" string — verified against the site's
    own JS bundle and its live result count.
    """
    if not job_meta:
        return False

    employment_type = job_meta.get('employmentType')
    if not isinstance(employment_type, str) or employment_type.lower() not in config.CAREERS_GOV_FULL_TIME_EMPLOYMENT_TYPES:
        return False

    experience_levels = job_meta.get('experienceLevels')
    if not isinstance(experience_levels, list):
        return False
    target = config.CAREERS_GOV_EXPERIENCE_LEVEL.lower()
    return any(isinstance(lvl, str) and lvl.lower() == target for lvl in experience_levels)

def _fetch_careers_gov_search_hits(query: str, hits_per_page: int) -> list:
    """
    Queries Careers@Gov's Algolia search index directly — the same call the site's own
    search bar makes client-side. Requires a matching Referer/Origin header since the
    public API key is referer-restricted.
    """
    headers = {
        'Content-Type': 'application/json',
        'Referer': config.CAREERS_GOV_BASE_URL + '/',
        'Origin': config.CAREERS_GOV_BASE_URL,
        'User-Agent': random.choice(MODERN_USER_AGENTS),
    }
    params = {
        'x-algolia-application-id': config.CAREERS_GOV_ALGOLIA_APP_ID,
        'x-algolia-api-key': config.CAREERS_GOV_ALGOLIA_API_KEY,
    }
    payload = {'query': query, 'hitsPerPage': hits_per_page, 'page': 0}

    try:
        resp = requests.post(
            config.CAREERS_GOV_ALGOLIA_URL,
            headers=headers,
            params=params,
            json=payload,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to query Careers@Gov search for '{query}': {e}")
        return []

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse Careers@Gov search response for '{query}': {e}")
        return []

    hits = data.get('hits') or []
    logging.info(f"Careers@Gov search for '{query}' returned {len(hits)} hit(s) (of {data.get('nbHits')} total matching).")
    return hits

def _careers_gov_detail_url(object_id: str) -> str | None:
    """
    Builds the job detail page URL from an Algolia objectID. The part after the source
    prefix already matches the site's URL path suffix (e.g. "HRP:{id}/{uuid}" -> /jobs/hrp/{id}/{uuid}).
    """
    if object_id.startswith("HRP:"):
        return f"{config.CAREERS_GOV_BASE_URL}/jobs/hrp/{object_id[4:]}"
    if object_id.startswith("GREENHOUSE:"):
        return f"{config.CAREERS_GOV_BASE_URL}/jobs/greenhouse/{object_id[11:]}"
    return None

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

def _fetch_careers_gov_description(object_id: str) -> str | None:
    """
    Fetches the job's own detail page and extracts its description from the JobPosting
    JSON-LD as real HTML. Algolia's search index flattens descriptions to plain text and
    loses structure (bullets become bare "•" with no paragraph breaks, HTML entities like
    &#39; go undecoded) — the detail page has proper <p>/<li>/<strong> tags instead, run
    through the same convert_html_to_markdown pipeline every other source uses.
    """
    detail_url = _careers_gov_detail_url(object_id)
    if not detail_url:
        logging.warning(f"Unrecognized Careers@Gov objectID format: {object_id}")
        return None

    logging.info(f"Preparing to fetch full description for Careers@Gov job: {object_id}")
    sleep_time = random.uniform(3.0, 10.0)
    logging.info(f"Waiting for {sleep_time:.2f} seconds before fetching details...")
    time.sleep(sleep_time)

    headers = {'User-Agent': random.choice(MODERN_USER_AGENTS)}
    logging.info(f"Fetching details from: {detail_url}")

    resp = None
    retries = 0
    while retries <= config.MAX_RETRIES:
        try:
            resp = requests.get(detail_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                retries += 1
                wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5)
                logging.warning(f"Error 429 for {object_id}. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                headers = {'User-Agent': random.choice(MODERN_USER_AGENTS)}
                continue
            elif e.response.status_code == 404:
                logging.warning(f"Job details not found (404) for Careers@Gov job: {object_id}.")
                return None
            else:
                logging.error(f"HTTP Error fetching details for Careers@Gov job {object_id}: {e}")
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Request Exception fetching details for Careers@Gov job {object_id}: {e}")
            return None

    if resp is None:
        logging.error(f"Failed to fetch details for Careers@Gov job {object_id} after {retries} retries (unexpected state).")
        return None

    posting = _extract_job_posting_ld_json(resp.text)
    if not posting:
        logging.warning(f"No JobPosting JSON-LD found for Careers@Gov job {object_id}.")
        return None

    content_html = posting.get('description') or ''
    if not content_html.strip():
        logging.warning(f"Description HTML was empty for Careers@Gov job {object_id}.")
        return None

    return _normalize_bullet_lines(convert_html_to_markdown(content_html))

def _normalize_bullet_lines(markdown_text: str) -> str:
    """
    Some Careers@Gov postings author bullet lists as literal "• " text with raw \\n between
    items instead of real <li> markup, so convert_html_to_markdown passes them through
    unchanged. "•" isn't a valid CommonMark list marker, so the frontend's markdown
    renderer treats the whole run as one paragraph regardless of the newlines. Converting
    to "- " (and ensuring a blank line precedes the first item) makes it a real list.
    """
    lines = markdown_text.split('\n')
    out = []
    prev_was_bullet = False
    for line in lines:
        stripped = line.strip()
        is_bullet = stripped.startswith('•')
        if is_bullet:
            content = stripped[1:].strip()
            if not prev_was_bullet and out and out[-1].strip():
                out.append('')
            out.append(f"- {content}")
        else:
            out.append(line)
        prev_was_bullet = is_bullet
    return '\n'.join(out)

def _careers_gov_hit_to_job_details(hit: dict) -> dict | None:
    """Converts a single Algolia search hit into the standard job_details dict, fetching
    the real description from the job's own detail page (see _fetch_careers_gov_description)."""
    object_id = hit.get('objectID')
    title = hit.get('title')
    if not object_id or not title:
        return None

    description = _fetch_careers_gov_description(object_id)

    posted_at = None
    activity_ts = hit.get('activityTimestamp')
    if activity_ts:
        try:
            posted_at = datetime.fromtimestamp(int(activity_ts) / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            posted_at = None

    return {
        "job_id": object_id,
        "company": hit.get('agency'),
        "job_title": title,
        "location": "Singapore",
        "level": None,
        "provider": "careers_gov",
        "description": description,
        "posted_at": posted_at,
    }

def process_careers_gov_query(search_query: str, job_dataset: dict, limit: int = None) -> list:
    """
    Orchestrates search + filtering for a single Careers@Gov query, mirroring the other
    sources: fetch a batch of hits, filter against Supabase, apply the
    employment-type/experience-level filter via job_dataset (see
    _fetch_careers_gov_job_dataset), truncate to limit, then fetch each surviving job's
    own detail page for its real (properly structured) description.
    """
    # Broader headroom than the other sources' 4x: the employment-type/experience-level
    # filter is applied after this fetch and typically only keeps a small fraction of
    # hits (verified live: ~1% of all Careers@Gov jobs are both Full-time and 0-1 year).
    fetch_size = max(50, (limit or 5) * 10)
    hits = _fetch_careers_gov_search_hits(search_query, hits_per_page=fetch_size)
    if not hits:
        logging.info(f"No search hits for Careers@Gov query '{search_query}'.")
        return []

    logging.info("\n--- Starting Filtering Step: Checking against Supabase ---")
    job_ids_set, _ = supabase_utils.get_existing_jobs_from_supabase()

    new_hits = [h for h in hits if h.get('objectID') and h['objectID'] not in job_ids_set]
    logging.info(f"Found {len(hits)} hits, {len(job_ids_set)} existing IDs in Supabase, {len(new_hits)} new.")

    if not new_hits:
        return []

    filtered_hits = []
    for h in new_hits:
        if job_title_is_excluded(h.get('title')):
            continue
        job_id_key = _careers_gov_job_id_from_object_id(h['objectID'])
        job_meta = job_dataset.get(job_id_key) if job_id_key else None
        if _careers_gov_matches_filters(job_meta):
            filtered_hits.append(h)
    logging.info(f"{len(filtered_hits)} of {len(new_hits)} new hits are Full-time + {config.CAREERS_GOV_EXPERIENCE_LEVEL} experience (after excluded-keyword filter).")
    new_hits = filtered_hits

    if not new_hits:
        return []

    if limit is not None and len(new_hits) > limit:
        logging.info(f"Truncating from {len(new_hits)} to {limit} to stay within source limit.")
        new_hits = new_hits[:limit]

    detailed_new_jobs = []
    for hit in new_hits:
        details = _careers_gov_hit_to_job_details(hit)
        if not details:
            logging.warning(f"Skipping malformed Careers@Gov hit: {hit.get('objectID')}")
            continue
        description = details.get('description')
        if not (description and description.strip()):
            logging.warning(f"Skipping job ID {details['job_id']} due to missing or empty description.")
            continue
        detailed_new_jobs.append(details)

    logging.info(f"--- Finished: {len(detailed_new_jobs)} new job(s) for query '{search_query}' ---")
    return detailed_new_jobs

# --- Main Execution ---
if __name__ == "__main__":

    total_new_jobs_saved = 0

    if "careers_gov" in config.SELF_HOSTED_SCRAPING_SOURCES:
        logging.info("\n--- Starting Careers@Gov Job Scraping ---")
        max_jobs_per_search = config.MAX_JOBS_PER_SEARCH.get("careers_gov", getattr(config, 'DEFAULT_MAX_JOBS_PER_SEARCH', 5))
        careers_gov_job_dataset = _fetch_careers_gov_job_dataset()
        for query in config.CAREERS_GOV_SEARCH_QUERIES:
            print(f"\n{'='*20} Processing Careers@Gov Search Query: '{query}' {'='*20}")

            new_careers_gov_jobs = process_careers_gov_query(query, careers_gov_job_dataset, limit=max_jobs_per_search)

            if new_careers_gov_jobs:
                print(f"\n--- Saving {len(new_careers_gov_jobs)} new job(s) for query '{query}' ---")
                supabase_utils.save_jobs_to_supabase(new_careers_gov_jobs)
                total_new_jobs_saved += len(new_careers_gov_jobs)
            else:
                print(f"\nNo new job details were fetched or processed for query '{query}'.")
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
