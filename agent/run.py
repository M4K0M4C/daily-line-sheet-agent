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

def tool_download_one_pexels_image(config: AgentConfig, run_root: Path, api_key: str) -> Path:
    """
    Tool: search Pexels for the topic and download exactly one image.

    Saves the image into: runs/<run_id>/raw/
    Returns the Path to the downloaded file.
    """
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": api_key}
    params = {"query": config.topic, "per_page": 1}

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    photos = data.get("photos", [])
    if not photos:
        raise RuntimeError(f"No Pexels results for topic: {config.topic}")

    photo = photos[0]
    src = photo.get("src", {})
    image_url = src.get("original") or src.get("large2x") or src.get("large")
    if not image_url:
        raise RuntimeError("Pexels response missing image URL")

    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()

    out_path = run_root / "raw" / "pexels_0001.jpg"
    out_path.write_bytes(img_resp.content)
    return out_path

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

    downloaded_image = tool_download_one_pexels_image(config, run_root, api_key)
    print(f"Downloaded: {downloaded_image}")

    print(plan)
    print(f"Run ID: {run_id}")

if __name__ == "__main__":
    main()
