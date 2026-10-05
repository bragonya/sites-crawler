from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from .runner import run_crawl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stackline-crawl")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Ejecuta una corrida y escribe el JSON en build/")
    run.add_argument("--input", type=Path)
    run.add_argument("--run-id", default=None)
    run.add_argument("--site-key", default="san-miguel")
    run.add_argument("--site-url")
    run.add_argument("--sop", type=Path)
    run.add_argument("--prompt")
    run.add_argument("--max-pages", type=int)
    run.add_argument("--baseline-products", type=int)
    run.add_argument("--build-root", type=Path, default=Path("build"))
    run.add_argument("--model", default=os.getenv("AI_CHAT_MODEL", "gpt-6-luna"))
    args = parser.parse_args(argv)

    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        if not args.site_url:
            parser.error("run requiere --site-url o --input")
        payload = {
            "run_id": args.run_id or str(uuid.uuid4()),
            "site_key": args.site_key,
            "site_url": args.site_url,
            "sop": args.sop.read_text(encoding="utf-8") if args.sop else "",
            "user_prompt": args.prompt,
            "max_pages": args.max_pages,
            "baseline_products": args.baseline_products,
            "build_root": str(args.build_root),
            "model": args.model,
        }
    payload.setdefault("build_root", str(args.build_root))
    payload.setdefault("model", args.model)
    result = run_crawl(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "COMPLETED" else 2 if result["status"] == "QUALITY_FAILED" else 3


if __name__ == "__main__":
    sys.exit(main())
