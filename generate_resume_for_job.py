"""
CLI entry point: generate a customized resume for a single job on demand.

Usage:
    python generate_resume_for_job.py <job_id>

Triggered by the "Generate Resume" button in jobs-scraper-web via a local
subprocess call (see src/app/api/jobs/[job_id]/generate-resume/route.ts).
Reuses the same personalize -> PDF -> upload -> save pipeline as the batch
cycle in custom_resume_generator.py (process_job), just scoped to the one
job_id the user clicked instead of the top-N RPC batch.

Prints a single-line JSON object to stdout on success:
    {"job_id": "...", "customized_resume_id": "..."}
and a JSON error object to stderr with a non-zero exit code on failure.
"""

import asyncio
import json
import logging
import os
import sys

import config
import supabase_utils
from custom_resume_generator import process_job
from models import Resume

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def _fail(message: str) -> int:
    print(json.dumps({"error": message}), file=sys.stderr)
    return 1


def _get_customized_resume_id(job_id: str) -> str | None:
    response = (
        supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
        .select("customized_resume_id")
        .eq("job_id", job_id)
        .limit(1)
        .execute()
    )
    if response.data:
        return response.data[0].get("customized_resume_id")
    return None


def _load_base_resume() -> dict | None:
    raw = supabase_utils.get_base_resume()
    if raw:
        return raw
    resume_path = getattr(config, "BASE_RESUME_PATH", "resume.json")
    if os.path.exists(resume_path):
        with open(resume_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


async def main(job_id: str) -> int:
    job = supabase_utils.get_job_by_id(job_id)
    if not job:
        return _fail(f"Job {job_id} not found in Supabase.")
    job["job_id"] = job_id  # get_job_by_id's select doesn't include it

    raw_resume_details = _load_base_resume()
    if not raw_resume_details:
        return _fail(
            "No base resume found in Supabase 'base_resume' table or resume.json. "
            "Run the 'Parse Resume' workflow first."
        )

    for key in ["skills", "experience", "education", "projects", "certifications", "languages"]:
        if raw_resume_details.get(key) is None:
            raw_resume_details[key] = []

    try:
        base_resume_details = Resume(**raw_resume_details)
    except Exception as e:
        return _fail(f"Failed to parse base resume: {e}")

    # process_job() logs and swallows its own errors instead of raising, so
    # success is verified by re-checking whether a *new* resume got attached
    # rather than trusting a return value.
    previous_resume_id = _get_customized_resume_id(job_id)

    await process_job(job, base_resume_details)

    customized_resume_id = _get_customized_resume_id(job_id)
    if not customized_resume_id or customized_resume_id == previous_resume_id:
        return _fail(
            "Resume generation did not complete successfully. Check job-scraper logs above for details."
        )

    print(json.dumps({"job_id": job_id, "customized_resume_id": customized_resume_id}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit(_fail("Usage: python generate_resume_for_job.py <job_id>"))
    sys.exit(asyncio.run(main(sys.argv[1].strip())))
