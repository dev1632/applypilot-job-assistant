"""Command-line entry points, e.g. for a launchd-scheduled daily scan:
    python -m backend.cli scan
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict

from backend.api.main import load_profile
from backend.agents.job_discovery import run_scan
from backend.db import SessionLocal, init_db
from backend.services.llm import get_llm


def main() -> None:
    parser = argparse.ArgumentParser(prog="applypilot")
    parser.add_argument("command", choices=["scan", "init-db"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db()
    if args.command == "scan":
        with SessionLocal() as session:
            report = run_scan(session, load_profile(session), llm=get_llm())
        print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
