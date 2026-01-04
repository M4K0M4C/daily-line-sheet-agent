"""
Daily Line Sheet Agent – Entry Point

This program accepts a topic from the command line.
Later, this topic will guide image search and selection.
"""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/run.py <topic>")
        return

    topic = sys.argv[1]
    print(f"Daily Line Sheet Agent: run started")
    print(f"Topic: {topic}")


if __name__ == "__main__":
    main()
