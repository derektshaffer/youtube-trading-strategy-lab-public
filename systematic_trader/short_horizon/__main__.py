"""Offline CLI. No network, credentials, collector access or production orders."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess

from ..events import digest
from .contracts import dataset_from_dict
from .execution import Costs, ExecutionPolicy
from .experiments import Experiment, Harness, experiment_grid
from .replay import read_ticks, file_hash
from .validation import stress_plan, walk_forward_plan


def code_identity():
    package = Path(__file__).parent
    files = sorted(package.glob("*.py")) + [package.parent/"events.py", package.parent/"research_split.py"]
    hashes = {str(p.relative_to(package.parent)): file_hash(p) for p in files}
    root = package.parent.parent
    return dict(git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                module_hashes=hashes, code_hash=digest(hashes), strategy_version="transparent-long-baselines-v1")


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def run_file(dataset_path, config_path, output):
    document = json.loads(Path(dataset_path).read_text())
    dataset = dataset_from_dict(document["dataset"])
    config = json.loads(Path(config_path).read_text())
    experiment = Experiment(**config["experiment"])
    policy = ExecutionPolicy(**config.get("policy", {}))
    costs = Costs(**config.get("costs", {}))
    identity = code_identity()
    result = Harness(dataset, experiment, policy, costs).run(read_ticks(Path(dataset_path).parent/"events.jsonl", expected_sha256=document["events_sha256"]))
    if code_identity() != identity:
        raise ValueError("short_horizon_code_changed_during_run")
    result.pop("result_hash")
    result.update(code=identity, data_sha256=document["events_sha256"], seed=17,
                  stress_plan=stress_plan(experiment, policy, costs),
                  walk_forward_plan=walk_forward_plan(sorted(s.day for s in dataset.sessions)))
    result["experiment_id"] = digest(dict(code=identity, data=document, config=config))
    result["result_hash"] = digest(result)
    save_new(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--dataset", required=True); run.add_argument("--config", required=True); run.add_argument("--output", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--family", choices=list("ABCDE"), required=True); plan.add_argument("--output", required=True)
    imp = commands.add_parser("import-alpaca")
    for name in ("quotes", "trades", "dataset", "output"):
        imp.add_argument("--"+name, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        save_new(args.output, dict(family=args.family, experiments=[asdict(e) for e in experiment_grid(args.family)],
                                  status="PLANNED_NOT_RUN", execution_authority="none"))
    elif args.command == "run":
        result = run_file(args.dataset, args.config, args.output)
        print(json.dumps({k:result[k] for k in ("experiment_id", "admission", "research_status", "missing")}))
    else:
        from .io import export_alpaca
        result = export_alpaca(args.quotes, args.trades, args.output, json.loads(Path(args.dataset).read_text()))
        print(result["import_status"])


if __name__ == "__main__":
    main()
