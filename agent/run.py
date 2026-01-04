"""
Daily Line Sheet Agent – Entry Point

This program accepts a topic from the command line.
Later, this topic will guide image search and selection.
"""

import sys
import datetime
import uuid
from pathlib import Path

def decide_plan(topic: str) -> str:
    """
    Decide what kind of work the agent should perform.

    For now, this is a stub that just echoes the topic.
    Later, this is where agent reasoning will live.
    """
    return f"Plan created for topic: {topic}"

def tool_generate_run_id(base_dir: str = "runs") -> str:
    """
    Tool: create a unique run id and ensure the run folder exists.

    Returns the run_id string.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    run_id = f"{timestamp}_{uuid.uuid4().hex[:8]}"

    run_path = Path(base_dir) / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    return run_id

def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/run.py <topic>")
        return

    topic = sys.argv[1]

    print("Daily Line Sheet Agent: run started")
    print(f"Topic: {topic}")

    plan = decide_plan(topic)
    run_id = tool_generate_run_id()
    print(plan)
    print(f"Run ID: {run_id}")

if __name__ == "__main__":
    main()
    