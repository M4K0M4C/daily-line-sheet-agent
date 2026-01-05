"""
Daily Line Sheet Agent – Entry Point

This program accepts a topic from the command line.
Later, this topic will guide image search and selection.
"""

from logging import config
import sys
import datetime
import uuid
from pathlib import Path
from dataclasses import dataclass
import os
import requests
import json
import time
from PIL import Image

@dataclass
class AgentConfig:
    """
    Configuration for a single run of the agent.

    Think of this as the agent's 'settings panel'.
    """
    topic: str
    candidates_to_download: int = 10
    sheets_to_generate: int = 3
    base_dir: str = "runs"

def decide_plan(topic: str) -> str:
    """
    Decide what kind of work the agent should perform.

    For now, this is a stub that just echoes the topic.
    Later, this is where agent reasoning will live.
    """
    return f"Plan created for topic: {topic}"

def tool_get_pexels_key() -> str:
    """
    Tool: read the Pexels API key from the environment.

    We do this via an environment variable so we do not hard-code secrets in code.
    """
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise RuntimeError(
            "PEXELS_API_KEY is not set. Set it in your environment before running."
        )
    return key

def tool_download_pexels_images(config: AgentConfig, run_root: Path, api_key: str) -> list[Path]:
    """
    Tool: search Pexels for the topic and download N images.

    Saves into: runs/<run_id>/raw/
    Writes manifest: runs/<run_id>/review/pexels_manifest.json
    Returns list of Paths to downloaded files.
    """
    search_url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": api_key}

    # Pexels per_page max is typically 80; we fetch at most what we need.
    per_page = min(80, max(1, config.candidates_to_download))
    params = {"query": config.topic, "per_page": per_page}

    resp = requests.get(search_url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    photos = data.get("photos", [])
    if not photos:
        raise RuntimeError(f"No Pexels results for topic: {config.topic}")

    # Take only N candidates
    photos = photos[: config.candidates_to_download]

    downloaded_paths: list[Path] = []
    manifest: dict = {
        "topic": config.topic,
        "requested": config.candidates_to_download,
        "downloaded": 0,
        "items": [],
    }

    raw_dir = run_root / "raw"
    review_dir = run_root / "review"

    session = requests.Session()

    for idx, photo in enumerate(photos, start=1):
        src = photo.get("src", {})
        image_url = src.get("original") or src.get("large2x") or src.get("large")
        if not image_url:
            continue

        # Deterministic filename: pexels_0001.jpg, pexels_0002.jpg, ...
        out_path = raw_dir / f"pexels_{idx:04d}.jpg"

        # Basic rate-limit / retry handling
        for attempt in range(1, 4):
            img_resp = session.get(image_url, timeout=60)

            if img_resp.status_code == 429:
                retry_after = img_resp.headers.get("Retry-After")
                sleep_s = int(retry_after) if (retry_after and retry_after.isdigit()) else 5
                time.sleep(sleep_s)
                continue

            img_resp.raise_for_status()
            out_path.write_bytes(img_resp.content)
            downloaded_paths.append(out_path)

            manifest["items"].append(
                {
                    "index": idx,
                    "pexels_id": photo.get("id"),
                    "photographer": photo.get("photographer"),
                    "width": photo.get("width"),
                    "height": photo.get("height"),
                    "page_url": photo.get("url"),
                    "image_url": image_url,
                    "local_file": str(out_path).replace("\\", "/"),
                }
            )
            break

        # Gentle pacing (keeps you out of trouble even when not rate-limited)
        time.sleep(0.4)

    manifest["downloaded"] = len(downloaded_paths)

    manifest_path = review_dir / "pexels_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return downloaded_paths

def tool_screen_images(config: AgentConfig, run_root: Path) -> dict:
    """
    Tool: screen downloaded images for basic quality.

    Copies passing images from raw/ -> ok/
    Writes report to review/screening_report.json
    Returns the report dict.
    """
    raw_dir = run_root / "raw"
    ok_dir = run_root / "ok"
    review_dir = run_root / "review"

    min_short_side = 1600
    max_aspect_ratio = 2.5  # e.g., reject very wide panoramas

    report = {
        "criteria": {
            "min_short_side": min_short_side,
            "max_aspect_ratio": max_aspect_ratio,
        },
        "passed": [],
        "failed": [],
    }

    for img_path in sorted(raw_dir.glob("*.jpg")):
        entry = {"file": img_path.name}

        try:
            with Image.open(img_path) as im:
                width, height = im.size
                short_side = min(width, height)
                aspect_ratio = max(width, height) / short_side

                entry.update(
                    {
                        "width": width,
                        "height": height,
                        "short_side": short_side,
                        "aspect_ratio": round(aspect_ratio, 2),
                    }
                )

                if short_side < min_short_side:
                    entry["reason"] = "resolution_too_low"
                    report["failed"].append(entry)
                    continue

                if aspect_ratio > max_aspect_ratio:
                    entry["reason"] = "extreme_aspect_ratio"
                    report["failed"].append(entry)
                    continue

                # Passed
                target = ok_dir / img_path.name
                target.write_bytes(img_path.read_bytes())
                report["passed"].append(entry)

        except Exception as e:
            entry["reason"] = f"unreadable_image: {e}"
            report["failed"].append(entry)

    report_path = review_dir / "screening_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report

def tool_generate_run_id(config: AgentConfig) -> str:
    """
    Tool: create a unique run id and ensure the run folder exists.

    Returns the run_id string.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    run_id = f"{timestamp}_{uuid.uuid4().hex[:8]}"

    run_path = Path(config.base_dir) / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    return run_id

def tool_create_run_folders(config: AgentConfig, run_id: str) -> Path:
    """
    Tool: create the directory structure for a run.

    Returns the Path to the run root folder (runs/<run_id>/).
    """
    run_root = Path(config.base_dir) / run_id

    for name in ["raw", "ok", "picks", "sheets", "review"]:
        (run_root / name).mkdir(parents=True, exist_ok=True)

    return run_root

def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/run.py <topic>")
        return

    config = AgentConfig(topic=sys.argv[1])

    print("Daily Line Sheet Agent: run started")
    print(f"Topic: {config.topic}")
    print(f"Config: {config}")

    plan = decide_plan(config.topic)
    run_id = tool_generate_run_id(config)
    
    run_root = tool_create_run_folders(config, run_id)
    print(f"Run folder: {run_root}")
    
    api_key = tool_get_pexels_key()
    print("Pexels key: found")

    downloaded_images = tool_download_pexels_images(config, run_root, api_key)
    print(f"Downloaded {len(downloaded_images)} images")
    print(f"Manifest: {run_root / 'review' / 'pexels_manifest.json'}")

    screening = tool_screen_images(config, run_root)
    print(f"Screened images — passed: {len(screening['passed'])}, failed: {len(screening['failed'])}")
    print(f"Screening report: {run_root / 'review' / 'screening_report.json'}")

    print(plan)
    print(f"Run ID: {run_id}")

if __name__ == "__main__":
    main()
