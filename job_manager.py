import asyncio
import httpx
import requests
import random
import time
from datetime import datetime, timedelta, timezone
import logging

# Import shared modules
import config
import user_agents
from supabase_utils import supabase # Use the initialized Supabase client
from selfhosted_scraper import _extract_seek_redux_data, _careers_gov_detail_url, MODERN_USER_AGENTS

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---

def get_utc_now() -> datetime:
    """Returns the current time in UTC."""
    return datetime.now(timezone.utc)

def get_past_date(days: int) -> datetime:
    """Returns the datetime object for a specific number of days ago in UTC."""
    return get_utc_now() - timedelta(days=days)

async def _check_single_linkedin_job_active(job_id: str, client: httpx.AsyncClient) -> bool | None:
    """
    Checks if a single LinkedIn job is still active.
    Returns:
        True if the job appears inactive (404, redirect, specific text).
        False if the job appears active.
        None if the check failed after retries.
    """
    job_detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    retries = 0
    inactive_keywords = ["this job is no longer available", "job is closed", "No longer accepting applications"] # Add more if needed


    while retries <= config.ACTIVE_CHECK_MAX_RETRIES:
        try:
            sleep_time = random.uniform(5.0, 15.0)
            logging.info(f"Waiting for {sleep_time:.2f} seconds before next request...")
            time.sleep(sleep_time)

            # Rotate user agent and proxy for each attempt
            user_agent = random.choice(user_agents.USER_AGENTS)
            headers = {'User-Agent': user_agent}

            logging.debug(f"Checking job {job_id} (Attempt {retries+1}/{config.ACTIVE_CHECK_MAX_RETRIES+1}) URL: {job_detail_url} with UA: {user_agent}")

            response = await client.get(
                job_detail_url,
                headers=headers,
                timeout=config.ACTIVE_CHECK_TIMEOUT,
                follow_redirects=True # Allow redirects to check final destination
            )

            # Check for 404 specifically
            if response.status_code == 404:
                logging.info(f"Job {job_id} returned 404. Marking as inactive.")
                return True

            # Check for other non-successful status codes (could indicate removal, private, etc.)
            # Allow redirects (3xx) as httpx handles them by default with follow_redirects=True
            if response.status_code >= 400:
                 logging.warning(f"Job {job_id} check failed with status {response.status_code}. Assuming active for now.")
                 # Decide if other errors mean inactive. For now, only 404 is definitive.
                 # Could return True here for stricter checking.
                 return False # Or None if we want to retry later

            # Check content for inactive keywords
            response_text_lower = response.text.lower()
            for keyword in inactive_keywords:
                if keyword in response_text_lower:
                    logging.info(f"Job {job_id} contains inactive keyword '{keyword}'. Marking as inactive.")
                    return True

            # If status is OK and no inactive keywords found
            logging.debug(f"Job {job_id} appears active (Status: {response.status_code}).")
            return False

        except httpx.TimeoutException:
            logging.warning(f"Timeout checking job {job_id} (Attempt {retries+1}).")
        except httpx.RequestError as e:
            logging.warning(f"Request error checking job {job_id} (Attempt {retries+1}): {e}")
        except Exception as e:
            logging.error(f"Unexpected error checking job {job_id} (Attempt {retries+1}): {e}")

        retries += 1
        if retries <= config.ACTIVE_CHECK_MAX_RETRIES:
            wait_time = config.ACTIVE_CHECK_RETRY_DELAY + random.uniform(0, 5)
            logging.info(f"Retrying job {job_id} check after {wait_time:.2f} seconds...")
            await asyncio.sleep(wait_time)

    logging.error(f"Failed to check job {job_id} activity after {config.ACTIVE_CHECK_MAX_RETRIES + 1} attempts.")
    return None # Failed to determine status


async def _check_single_careers_future_job_active(job_id: str, client: httpx.AsyncClient) -> bool | None:
    """Checks if a single MyCareersFuture job is still active via its detail API."""
    api_url = f"https://api.mycareersfuture.gov.sg/v2/jobs/{job_id}"
    retries = 0

    while retries <= config.ACTIVE_CHECK_MAX_RETRIES:
        try:
            sleep_time = random.uniform(5.0, 15.0)
            logging.info(f"Waiting for {sleep_time:.2f} seconds before next request...")
            time.sleep(sleep_time)

            headers = {'User-Agent': random.choice(user_agents.USER_AGENTS)}
            response = await client.get(api_url, headers=headers, timeout=config.ACTIVE_CHECK_TIMEOUT)

            if response.status_code == 404:
                logging.info(f"CareersFuture job {job_id} returned 404. Marking as inactive.")
                return True
            if response.status_code >= 400:
                logging.warning(f"CareersFuture job {job_id} check failed with status {response.status_code}. Assuming active for now.")
                return False

            return False

        except httpx.TimeoutException:
            logging.warning(f"Timeout checking CareersFuture job {job_id} (Attempt {retries+1}).")
        except httpx.RequestError as e:
            logging.warning(f"Request error checking CareersFuture job {job_id} (Attempt {retries+1}): {e}")
        except Exception as e:
            logging.error(f"Unexpected error checking CareersFuture job {job_id} (Attempt {retries+1}): {e}")

        retries += 1
        if retries <= config.ACTIVE_CHECK_MAX_RETRIES:
            wait_time = config.ACTIVE_CHECK_RETRY_DELAY + random.uniform(0, 5)
            await asyncio.sleep(wait_time)

    logging.error(f"Failed to check CareersFuture job {job_id} activity after {config.ACTIVE_CHECK_MAX_RETRIES + 1} attempts.")
    return None


# JobStreet's WAF (Akamai-style bot detection) reliably 403-blocks httpx's async client
# even after retries with heavy backoff, but never blocks `requests` — same headers, same
# runner IP, so it's a TLS/HTTP2 client-fingerprint check, not a rate limit. Confirmed live:
# httpx got 403'd 5/5 times over ~4 minutes of backoff; the scraper's requests-based fetch
# to the same URL pattern from the same runner pool has never been blocked. So this check
# runs synchronously via `requests` (in a thread, to stay compatible with the async gather
# in check_job_activity) instead of sharing the httpx.AsyncClient the other providers use.
def _fetch_jobstreet_active_sync(job_id: str) -> bool | None:
    """Blocking check via `requests`, matching the scraper's proven-working client fingerprint."""
    detail_url = f"{config.JOBSTREET_BASE_URL}/job/{job_id}"
    headers = {'User-Agent': random.choice(MODERN_USER_AGENTS)}
    retries = 0

    while retries <= config.ACTIVE_CHECK_MAX_RETRIES:
        try:
            resp = requests.get(detail_url, headers=headers, timeout=config.ACTIVE_CHECK_TIMEOUT)

            if resp.status_code == 404:
                logging.info(f"JobStreet job {job_id} returned 404. Marking as inactive.")
                return True
            if resp.status_code >= 400:
                logging.warning(f"JobStreet job {job_id} check failed with status {resp.status_code}. Assuming active for now.")
                return False

            redux_data = _extract_seek_redux_data(resp.text)
            job = ((redux_data or {}).get('jobdetails', {}) or {}).get('result', {}).get('job')
            if not job:
                logging.warning(f"No job data found in SEEK_REDUX_DATA for JobStreet job {job_id}. Assuming active for now.")
                return False
            if job.get('isExpired'):
                logging.info(f"JobStreet job {job_id} is flagged isExpired. Marking as inactive.")
                return True

            return False

        except requests.exceptions.RequestException as e:
            logging.warning(f"Request error checking JobStreet job {job_id} (Attempt {retries+1}): {e}")

        retries += 1
        if retries <= config.ACTIVE_CHECK_MAX_RETRIES:
            time.sleep(config.ACTIVE_CHECK_RETRY_DELAY + random.uniform(0, 5))

    logging.error(f"Failed to check JobStreet job {job_id} activity after {config.ACTIVE_CHECK_MAX_RETRIES + 1} attempts.")
    return None


async def _check_single_jobstreet_job_active(job_id: str, client: httpx.AsyncClient) -> bool | None:
    """Checks if a single JobStreet job is still active (404, or the listing's own isExpired flag)."""
    sleep_time = random.uniform(5.0, 15.0)
    logging.info(f"Waiting for {sleep_time:.2f} seconds before next request...")
    await asyncio.sleep(sleep_time)
    return await asyncio.to_thread(_fetch_jobstreet_active_sync, job_id)


async def _check_single_careers_gov_job_active(job_id: str, client: httpx.AsyncClient) -> bool | None:
    """Checks if a single Careers@Gov job is still active via its own detail page."""
    detail_url = _careers_gov_detail_url(job_id)
    if not detail_url:
        logging.warning(f"Unrecognized Careers@Gov objectID format: {job_id}. Skipping activity check.")
        return None

    retries = 0
    while retries <= config.ACTIVE_CHECK_MAX_RETRIES:
        try:
            sleep_time = random.uniform(5.0, 15.0)
            logging.info(f"Waiting for {sleep_time:.2f} seconds before next request...")
            time.sleep(sleep_time)

            headers = {'User-Agent': random.choice(MODERN_USER_AGENTS)}
            response = await client.get(detail_url, headers=headers, timeout=config.ACTIVE_CHECK_TIMEOUT)

            if response.status_code == 404:
                logging.info(f"Careers@Gov job {job_id} returned 404. Marking as inactive.")
                return True
            if response.status_code >= 400:
                logging.warning(f"Careers@Gov job {job_id} check failed with status {response.status_code}. Assuming active for now.")
                return False

            return False

        except httpx.TimeoutException:
            logging.warning(f"Timeout checking Careers@Gov job {job_id} (Attempt {retries+1}).")
        except httpx.RequestError as e:
            logging.warning(f"Request error checking Careers@Gov job {job_id} (Attempt {retries+1}): {e}")
        except Exception as e:
            logging.error(f"Unexpected error checking Careers@Gov job {job_id} (Attempt {retries+1}): {e}")

        retries += 1
        if retries <= config.ACTIVE_CHECK_MAX_RETRIES:
            wait_time = config.ACTIVE_CHECK_RETRY_DELAY + random.uniform(0, 5)
            await asyncio.sleep(wait_time)

    logging.error(f"Failed to check Careers@Gov job {job_id} activity after {config.ACTIVE_CHECK_MAX_RETRIES + 1} attempts.")
    return None


# Maps a job's 'provider' column to its activity-check coroutine. Providers not listed
# here (if any new scraper is added later without a check function) are simply skipped
# by check_job_activity() rather than erroring.
_PROVIDER_ACTIVE_CHECKS = {
    "linkedin": _check_single_linkedin_job_active,
    "careers_future": _check_single_careers_future_job_active,
    "jobstreet": _check_single_jobstreet_job_active,
    "careers_gov": _check_single_careers_gov_job_active,
}

# --- Main Management Functions ---

async def mark_expired_jobs():
    """Marks old jobs (not applied/interviewing) as expired."""
    logging.info("--- Starting Task: Mark Expired Jobs ---")
    expiry_date = get_past_date(config.JOB_EXPIRY_DAYS)
    # Format for Supabase timestampz query
    expiry_date_str = expiry_date.isoformat()
    excluded_statuses = ['applied', 'offer', 'interviewing'] # Add any status that means "don't expire"

    try:
        # Select jobs to expire
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
            .select("job_id")\
            .lt("scraped_at", expiry_date_str)\
            .not_.in_("status", excluded_statuses)\
            .eq("is_active", True)\
            .execute()

        if response.data:
            job_ids_to_expire = [job['job_id'] for job in response.data]
            logging.info(f"Found {len(job_ids_to_expire)} jobs older than {config.JOB_EXPIRY_DAYS} days to mark as expired.")

            if job_ids_to_expire:
                # Update in batches if necessary, though supabase-py might handle large lists
                # For simplicity, updating all at once here. Consider batching for >1000s of IDs.
                update_response = supabase.table(config.SUPABASE_TABLE_NAME)\
                    .update({"job_state": "expired", "is_active": False})\
                    .in_("job_id", job_ids_to_expire)\
                    .execute()

                # Check response structure - may vary slightly
                if hasattr(update_response, 'data') and update_response.data:
                     updated_count = len(update_response.data) # Supabase often returns the updated rows
                     logging.info(f"Successfully marked {updated_count} jobs as expired.")
                elif hasattr(update_response, 'count') and update_response.count is not None:
                     logging.info(f"Successfully marked {update_response.count} jobs as expired (based on count).")
                else:
                     # Log raw response if structure is unexpected
                     logging.warning(f"Mark expired jobs update executed. Response: {update_response}")

        else:
            logging.info("No jobs found meeting the criteria for expiration.")

    except Exception as e:
        logging.error(f"Error marking expired jobs: {e}")

    logging.info("--- Finished Task: Mark Expired Jobs ---")


async def check_job_activity():
    """Checks if active jobs are still available on their source portal (linkedin,
    careers_future, jobstreet, careers_gov — any provider with a registered check
    function in _PROVIDER_ACTIVE_CHECKS)."""
    logging.info("--- Starting Task: Check Job Activity ---")
    check_older_than_date = get_past_date(config.JOB_CHECK_DAYS)
    check_older_than_date_str = check_older_than_date.isoformat()
    now_str = get_utc_now().isoformat()

    jobs_to_check = []
    try:
        # Query for jobs needing a check: active AND older than N days, across all
        # providers (not just LinkedIn). Order by last_checked ASC to prioritize
        # the oldest checks, and limit the number of checks per run.
        excluded_statuses = ['applied', 'offer', 'interviewing'] # Add any status that means "don't expire"
        query = supabase.table(config.SUPABASE_TABLE_NAME)\
            .select("job_id, provider, last_checked")\
            .eq("is_active", True)\
            .not_.in_("status", excluded_statuses)\
            .lt("last_checked", check_older_than_date_str)\
            .order("last_checked", desc=False)\
            .limit(config.JOB_CHECK_LIMIT)

        response = query.execute()

        if response.data:
            jobs_to_check = response.data
            logging.info(f"Found {len(jobs_to_check)} active jobs to check (limit: {config.JOB_CHECK_LIMIT}).")
        else:
            logging.info("No active jobs need checking currently.")
            return # Nothing to do

    except Exception as e:
        logging.error(f"Error fetching jobs to check: {e}")
        return # Cannot proceed

    # Use httpx.AsyncClient for connection pooling and efficiency
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = []
        for job in jobs_to_check:
            check_fn = _PROVIDER_ACTIVE_CHECKS.get(job.get('provider'))
            if check_fn is None:
                tasks.append(None) # No checker registered for this provider — skip live-checking it
            else:
                tasks.append(check_fn(job['job_id'], client))
        results = await asyncio.gather(*(t for t in tasks if t is not None), return_exceptions=True)

    # Re-align results (some entries were skipped, i.e. None placeholders) back onto jobs_to_check
    result_iter = iter(results)
    resolved = [next(result_iter) if t is not None else None for t in tasks]

    inactive_job_ids = []
    active_checked_job_ids = []
    failed_check_job_ids = []
    skipped_unknown_provider = []

    for i, result in enumerate(resolved):
        job_id = jobs_to_check[i]['job_id']
        provider = jobs_to_check[i].get('provider')
        if tasks[i] is None:
            # No checker for this provider — still refresh last_checked so it doesn't
            # permanently monopolize the oldest-first queue every run.
            skipped_unknown_provider.append(job_id)
            active_checked_job_ids.append(job_id)
        elif isinstance(result, Exception):
            logging.error(f"Exception checking job {job_id} ({provider}): {result}")
            failed_check_job_ids.append(job_id)
        elif result is True: # Job confirmed inactive
            inactive_job_ids.append(job_id)
        elif result is False: # Job confirmed active
            active_checked_job_ids.append(job_id)
        elif result is None: # Check failed after retries, or provider format unrecognized
            failed_check_job_ids.append(job_id)

    if skipped_unknown_provider:
        logging.warning(f"Skipped live-checking {len(skipped_unknown_provider)} job(s) with no registered provider checker: {skipped_unknown_provider}")

    logging.info(f"Activity Check Summary: Inactive={len(inactive_job_ids)}, Active={len(active_checked_job_ids)}, Failed={len(failed_check_job_ids)}")

    # Update Supabase
    try:
        if inactive_job_ids:
            update_inactive = supabase.table(config.SUPABASE_TABLE_NAME)\
                .update({"job_state": "expired", "is_active": False, "last_checked": now_str})\
                .in_("job_id", inactive_job_ids)\
                .execute()
            # Add logging for update_inactive response count/data
            logging.info(f"Marked {len(inactive_job_ids)} jobs as expired (no longer available on their source portal). Response: {update_inactive}")


        if active_checked_job_ids:
            update_active = supabase.table(config.SUPABASE_TABLE_NAME)\
                .update({"last_checked": now_str})\
                .in_("job_id", active_checked_job_ids)\
                .execute()
            # Add logging for update_active response count/data
            logging.info(f"Updated last_checked for {len(active_checked_job_ids)} active jobs.")

    except Exception as e:
        logging.error(f"Error updating job statuses after activity check: {e}")

    logging.info("--- Finished Task: Check Job Activity ---")


async def delete_old_inactive_jobs():
    """Permanently deletes very old inactive jobs."""
    logging.info("--- Starting Task: Delete Old Inactive Jobs ---")
    delete_older_than_date = get_past_date(config.JOB_DELETION_DAYS)
    delete_older_than_date_str = delete_older_than_date.isoformat()
    inactive_states = ['expired', 'removed']

    try:
        # Select jobs to delete
        # No need to select data, just filter and delete
        delete_response = supabase.table(config.SUPABASE_TABLE_NAME)\
            .delete()\
            .eq("is_active", False)\
            .in_("job_state", inactive_states)\
            .lt("scraped_at", delete_older_than_date_str)\
            .execute()

        # Check response structure for delete count
        deleted_count = 0
        if hasattr(delete_response, 'data') and delete_response.data:
             deleted_count = len(delete_response.data) # Delete often returns the deleted rows
        elif hasattr(delete_response, 'count') and delete_response.count is not None:
             deleted_count = delete_response.count

        if deleted_count > 0:
            logging.info(f"Successfully deleted {deleted_count} inactive jobs older than {config.JOB_DELETION_DAYS} days.")
        else:
            logging.info("No old inactive jobs found to delete.")
            # Log raw response if structure is unexpected but count is 0
            logging.debug(f"Delete response when no jobs matched: {delete_response}")


    except Exception as e:
        logging.error(f"Error deleting old inactive jobs: {e}")

    logging.info("--- Finished Task: Delete Old Inactive Jobs ---")


# --- Main Execution ---
async def main():
    """Runs the job management tasks."""
    logging.info("Starting Job Management Script...")
    start_time = time.time()

    await mark_expired_jobs()
    await check_job_activity()
    await delete_old_inactive_jobs()

    end_time = time.time()
    logging.info(f"Job Management Script finished in {end_time - start_time:.2f} seconds.")

if __name__ == "__main__":
    asyncio.run(main())