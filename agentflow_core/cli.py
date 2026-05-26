from __future__ import annotations

import argparse
import json
import sys

from agentflow_core.config import load_workflow_config
from agentflow_core.errors import WorkflowError
from agentflow_core.logger import configure_logging, log_step
from agentflow_core.workflow import run_workflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a YAML-driven multi-agent workflow.",
        formatter_class=lambda prog: argparse.HelpFormatter(
            prog,
            max_help_position=40,
            width=100,
        ),
    )
    parser.add_argument(
        "--config", required=True, help="Path to the shared workflow YAML file."
    )
    parser.add_argument("--jobs", required=True, help="Path to the jobs YAML file.")
    parser.add_argument(
        "--prompt-pack-path",
        help=(
            "Prompt packs directory containing <program.prompt_pack>/pack.yaml; overrides "
            "program.prompt_pack_path from YAML."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full prompt for each OpenCode invocation.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only errors and the final JSON result.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(quiet=args.quiet, verbose=args.verbose)

    try:
        workflow_config = load_workflow_config(
            args.config,
            args.jobs,
            prompt_pack_path=args.prompt_pack_path,
        )
        log_step(f"Loaded workflow config: {workflow_config.config_path}")
        log_step(f"Loaded jobs file: {workflow_config.jobs_path}")
        log_step(
            f"Loaded prompt pack '{workflow_config.program.prompt_pack}': "
            f"{workflow_config.prompt_pack_dir}"
        )
        result = run_workflow(workflow_config)
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
