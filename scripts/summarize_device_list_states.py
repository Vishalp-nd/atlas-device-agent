import argparse
import json
from collections import Counter


def load_device_list_json(file_path: str):
    with open(file_path) as jsonfile:
        return json.load(jsonfile)


def summarize_state_counts(device_list_data: dict):
    state_counts = Counter()

    for _, states in device_list_data.items():
        if not isinstance(states, dict):
            continue

        for state_name, device_ids in states.items():
            if not isinstance(device_ids, list):
                continue
            state_counts[state_name] += len(device_ids)

    return state_counts


def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize device counts by state across all OTA versions from a device_list.json file"
    )
    parser.add_argument(
        "device_list_json",
        help="Path to device_list.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print the summary as JSON instead of plain text",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    device_list_data = load_device_list_json(args.device_list_json)
    state_counts = summarize_state_counts(device_list_data)

    if args.json:
        print(json.dumps(dict(sorted(state_counts.items())), indent=2))
    else:
        for state_name, count in sorted(state_counts.items()):
            print(f"{state_name}: {count}")