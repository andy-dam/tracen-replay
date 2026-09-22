"""CLI entry point; only local files are accepted."""

import argparse
import json
import sys
from pathlib import Path

from .pipeline import PipelineError, analyze


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract timestamped replay evidence and a local HTML gallery.")
    parser.add_argument("source", type=Path, help="Local video file")
    parser.add_argument("--start", type=float, default=0, help="Source-relative start in seconds (default: 0)")
    parser.add_argument("--duration", type=float, default=57, help="Seconds to inspect, at most 120 (default: 57)")
    parser.add_argument("--fps", type=float, default=4, help="Sampling rate, 1–8 (default: 4)")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing directories are never overwritten")
    parser.add_argument("--annotations", type=Path, help="Optional source-hash-bound manual annotation JSON")
    args = parser.parse_args(argv)
    try:
        result = analyze(args.source, args.output, args.start, args.duration, args.fps, args.annotations)
    except (PipelineError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
