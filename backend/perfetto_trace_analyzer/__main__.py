"""CLI entry point for Perfetto Trace Analyzer."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import ConfigManager
from .orchestrator import Orchestrator, scan_trace_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perfetto-trace-analyzer",
        description="LLM-driven automated Android performance analysis tool for Perfetto traces.",
    )
    parser.add_argument(
        "trace_path",
        help="Path to a Perfetto trace file or directory containing trace files",
    )
    parser.add_argument(
        "--analyzers",
        nargs="+",
        default=None,
        help="List of analyzer names to run (default: all)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output report file path (default: <trace_name>_report.<format>)",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["json", "html"],
        default="json",
        dest="output_format",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to custom YAML config file",
    )
    parser.add_argument(
        "--package", "-p",
        default=None,
        help="Target application package name for filtering",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start web server with interactive dashboard and Perfetto UI integration",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Web server port (default: 8000)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load config
    config = ConfigManager.load(args.config)

    # Scan trace files
    trace_files = scan_trace_files(args.trace_path)
    if not trace_files:
        print(f"Error: No trace files found at {args.trace_path}", file=sys.stderr)
        return 1

    print(f"Found {len(trace_files)} trace file(s)")

    # Web server mode
    if args.serve:
        return _serve(args, config)

    # CLI mode
    orchestrator = Orchestrator(config)

    if len(trace_files) == 1:
        result = orchestrator.analyze(
            trace_files[0],
            analyzer_names=args.analyzers,
            package_filter=args.package,
        )
        results = [result]
    else:
        results = orchestrator.batch_analyze(
            trace_files,
            analyzer_names=args.analyzers,
            package_filter=args.package,
        )

    # Generate reports
    for result in results:
        output_path = args.output
        if output_path is None:
            base = os.path.splitext(os.path.basename(result.trace_path))[0]
            output_path = f"{base}_report.{args.output_format}"

        if args.output_format == "html":
            orchestrator.reporter.generate_html(result, output_path)
        else:
            orchestrator.reporter.generate_json(result, output_path)

        score_info = ""
        if result.overall_score:
            score_info = f" (Score: {result.overall_score.overall})"
        print(f"Report generated: {output_path}{score_info}")

    # Generate comparison report for batch analysis
    if len(results) > 1:
        comp_path = args.output or "comparison_report.json"
        comp_dir = os.path.dirname(comp_path) or "."
        comp_file = os.path.join(comp_dir, "comparison_report.json")
        orchestrator.reporter.generate_comparison(results, comp_file)
        print(f"Comparison report: {comp_file}")

    return 0


def _serve(args, config) -> int:
    """Start the web server with interactive dashboard."""
    import uvicorn
    from .server import create_app

    trace_path = os.path.abspath(args.trace_path)
    print(f"Analyzing trace(s) and starting web server...")

    create_app(config=config, trace_path=trace_path)

    # Pass trace_path via env so reload can re-initialize
    os.environ["_PTA_TRACE_PATH"] = trace_path
    if args.config:
        os.environ["_PTA_CONFIG"] = args.config

    print(f"\n  Backend API:  http://localhost:{args.port}/api/traces")
    print(f"  Perfetto UI:  http://localhost:{args.port}/")
    print(f"  Frontend:     http://localhost:5173 (run: cd ../frontend && npm run dev)\n")

    uvicorn.run(
        "perfetto_trace_analyzer.server:app",
        host="0.0.0.0",
        port=args.port,
        log_level="warning",
        reload=True,
        reload_dirs=[os.path.dirname(os.path.abspath(__file__))],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
