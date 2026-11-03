import pandas as pd

from evaluations import (
    BENCHMARK_SCENARIO_IDS,
    NUM_PARTS,
    PROJECT_ROOT,
    RESULTS_PATH,
    results_path_for_part,
)


def merge_results() -> None:
    frames = []
    for part in range(1, NUM_PARTS + 1):
        part_path = results_path_for_part(part)
        if part_path.exists():
            frames.append(pd.read_csv(part_path))
            print(f"Loaded {part_path.name}")
        else:
            print(f"Missing {part_path.name}; skipping it.")

    if not frames:
        raise SystemExit(f"No part files found in {PROJECT_ROOT}.")

    merged = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("scenario_id", keep="last")
        .sort_values("evaluation_case")
    )
    merged.to_csv(RESULTS_PATH, index=False)

    missing = sorted(set(BENCHMARK_SCENARIO_IDS) - set(merged["scenario_id"]))
    print(f"Saved {len(merged)} case(s) to {RESULTS_PATH.name}.")
    if missing:
        print(f"Still missing scenarios: {missing}")


if __name__ == "__main__":
    merge_results()
