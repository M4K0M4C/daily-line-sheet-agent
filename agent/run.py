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
import math
from PIL import ImageStat, ImageOps
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

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

def tool_select_best_images(config: AgentConfig, run_root: Path) -> dict:
    """
    Tool: select the best images from ok/ and copy to picks/.

    Writes review/selection_report.json
    Returns the report dict.
    """
    ok_dir = run_root / "ok"
    picks_dir = run_root / "picks"
    review_dir = run_root / "review"

    target_count = max(1, config.sheets_to_generate)

    # Portrait-friendly target ratios (w/h). We'll reward closeness.
    target_ratios = [4/5, 3/4, 2/3]

    scored = []

    for img_path in sorted(ok_dir.glob("*.jpg")):
        with Image.open(img_path) as im:
            w, h = im.size
            short_side = min(w, h)
            ratio = w / h

            # 1) Resolution score: normalize using log to reduce domination by huge images
            res_score = math.log(short_side)

            # 2) Aspect ratio score: closeness to any target ratio (higher is better)
            ar_dist = min(abs(ratio - tr) for tr in target_ratios)
            ar_score = 1 / (1 + ar_dist)  # in (0,1], closer => closer to 1

            # 3) Detail proxy: grayscale contrast (stddev) on a resized copy
            thumb = im.copy()
            thumb.thumbnail((800, 800))
            gray = ImageOps.grayscale(thumb)
            stat = ImageStat.Stat(gray)
            # stddev is a rough proxy for contrast/detail
            detail_score = stat.stddev[0] / 64.0  # scale roughly into ~0..2 range

            total = (res_score * 1.0) + (ar_score * 2.0) + (detail_score * 1.5)

            scored.append(
                {
                    "file": img_path.name,
                    "width": w,
                    "height": h,
                    "short_side": short_side,
                    "ratio_w_over_h": round(ratio, 3),
                    "scores": {
                        "resolution": round(res_score, 3),
                        "aspect": round(ar_score, 3),
                        "detail": round(detail_score, 3),
                    },
                    "total": round(total, 3),
                }
            )

    scored.sort(key=lambda x: x["total"], reverse=True)

    selected = scored[:target_count]

    # Copy selected files into picks/ with deterministic names
    for i, item in enumerate(selected, start=1):
        src = ok_dir / item["file"]
        dst = picks_dir / f"pick_{i:02d}.jpg"
        dst.write_bytes(src.read_bytes())
        item["picked_as"] = dst.name

    report = {
        "topic": config.topic,
        "target_count": target_count,
        "selected": selected,
        "all_scored": scored,
    }

    report_path = review_dir / "selection_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report

def tool_render_worksheet_pdfs(config: AgentConfig, run_root: Path) -> list[Path]:
    """
    Tool: render one PDF worksheet per picked image.

    Outputs to: runs/<run_id>/sheets/
    Returns list of created PDF paths.
    """
    picks_dir = run_root / "picks"
    sheets_dir = run_root / "sheets"

    page_w, page_h = letter  # 8.5 x 11 inches in points (612 x 792)

    pdf_paths: list[Path] = []

    # Very simple beginner-friendly tips template (topic-agnostic for now)
    tips = [
        "Block in big shapes first (gesture > detail).",
        "Measure angles with your pencil; compare heights/widths.",
        "Keep values simple: light, mid, dark. Refine later.",
        "Edges: sharp edges pull focus; soft edges recede.",
    ]

    rubric = [
        ("Proportions", "Major shapes match the reference (size/placement)."),
        ("Values", "Clear separation of light/mid/dark."),
        ("Edges", "Mix of soft and sharp edges used intentionally."),
        ("Finish", "Drawing is clean; details support the main form."),
    ]

    pick_files = sorted(picks_dir.glob("pick_*.jpg"))
    if not pick_files:
        raise RuntimeError("No picks found. Run selection first.")

    for i, img_path in enumerate(pick_files, start=1):
        pdf_path = sheets_dir / f"worksheet_{i:02d}.pdf"
        c = canvas.Canvas(str(pdf_path), pagesize=letter)

        # Margins and layout constants (points)
        margin = 36  # 0.5"
        top_y = page_h - margin

        # Header
        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, top_y, f"Daily Line — Day Sheet ({config.topic})")

        c.setFont("Helvetica", 10)
        c.drawString(margin, top_y - 16, f"Reference: {img_path.name}")

        # Reference image box (top-left)
        img_box_w = 240
        img_box_h = 180
        img_x = margin
        img_y = top_y - 16 - 14 - img_box_h  # below header

        c.setLineWidth(1)
        c.rect(img_x, img_y, img_box_w, img_box_h)

        # Draw image fit-in-box (preserve aspect)
        with Image.open(img_path) as im:
            iw, ih = im.size
        scale = min(img_box_w / iw, img_box_h / ih)
        draw_w = iw * scale
        draw_h = ih * scale
        dx = img_x + (img_box_w - draw_w) / 2
        dy = img_y + (img_box_h - draw_h) / 2
        c.drawImage(ImageReader(str(img_path)), dx, dy, draw_w, draw_h, preserveAspectRatio=True, mask='auto')

        # Tips box (top-right)
        tips_x = img_x + img_box_w + 24
        tips_w = page_w - margin - tips_x
        tips_h = img_box_h
        tips_y = img_y

        c.rect(tips_x, tips_y, tips_w, tips_h)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(tips_x + 10, tips_y + tips_h - 18, "Tips (do these first)")

        c.setFont("Helvetica", 10)
        ty = tips_y + tips_h - 34
        for t in tips:
            c.drawString(tips_x + 10, ty, f"• {t}")
            ty -= 14

        # Drawing space (big box)
        draw_box_x = margin
        draw_box_y = margin + 140  # leave space for rubric at bottom
        draw_box_w = page_w - 2 * margin
        draw_box_h = (img_y - 24) - draw_box_y  # between top area and rubric
        c.rect(draw_box_x, draw_box_y, draw_box_w, draw_box_h)

        c.setFont("Helvetica-Oblique", 10)
        c.drawString(draw_box_x + 10, draw_box_y + draw_box_h - 16, "Drawing space (light construction lines first)")

        # Rubric box (bottom)
        rubric_x = margin
        rubric_y = margin
        rubric_w = page_w - 2 * margin
        rubric_h = 120
        c.rect(rubric_x, rubric_y, rubric_w, rubric_h)

        c.setFont("Helvetica-Bold", 12)
        c.drawString(rubric_x + 10, rubric_y + rubric_h - 18, "Self-Critique Rubric (0–4 each)")

        c.setFont("Helvetica", 9)
        ry = rubric_y + rubric_h - 36
        for name, desc in rubric:
            c.drawString(rubric_x + 10, ry, f"{name}: {desc}")
            # score boxes
            sx = rubric_x + rubric_w - 120
            c.drawString(sx - 40, ry, "Score:")
            for k in range(5):
                c.rect(sx + k * 18, ry - 8, 14, 14)
            ry -= 18

        c.setFont("Helvetica-Oblique", 9)
        c.drawString(rubric_x + 10, rubric_y + 10, "Optional: write 1 improvement and 1 strength on the back.")

        c.showPage()
        c.save()

        pdf_paths.append(pdf_path)

    return pdf_paths

def tool_write_approval_placeholder(run_root: Path) -> Path:
    """
    Tool: write a human approval placeholder file.

    The user edits this file to approve or reject the run.
    """
    review_dir = run_root / "review"
    approval = {
        "status": "PENDING",  # APPROVED | REJECTED
        "reviewer": "",
        "notes": "",
    }

    path = review_dir / "approval.json"
    path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
    return path

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

    selection = tool_select_best_images(config, run_root)
    print(f"Selected {len(selection['selected'])} picks")
    print(f"Selection report: {run_root / 'review' / 'selection_report.json'}")

    pdfs = tool_render_worksheet_pdfs(config, run_root)
    print(f"Rendered {len(pdfs)} PDFs")
    print(f"Sheets folder: {run_root / 'sheets'}")

    approval_path = tool_write_approval_placeholder(run_root)
    print(f"Awaiting human approval: {approval_path}")

    print(plan)
    print(f"Run ID: {run_id}")

if __name__ == "__main__":
    main()
