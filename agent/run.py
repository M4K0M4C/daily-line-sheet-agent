"""
Daily Line Sheet Agent – Entry Point

This program accepts a topic from the command line.
Later, this topic will guide image search and selection.
"""

import sys


def decide_plan(topic: str) -> str:
    """
    Decide what kind of work the agent should perform.

    For now, this is a stub that just echoes the topic.
    Later, this is where agent reasoning will live.
    """
    return f"Plan created for topic: {topic}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/run.py <topic>")
        return

    topic = sys.argv[1]

    print("Daily Line Sheet Agent: run started")
    print(f"Topic: {topic}")

    plan = decide_plan(topic)
    print(plan)


if __name__ == "__main__":
    main()
