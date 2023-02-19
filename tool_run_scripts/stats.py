import sys
from threading import Lock
import json
from datetime import datetime

class Stats:
    def __init__(self):
        self.lock = Lock()
        self.stats = {}
        self.rules = {}

    def load_json(self, fil):
        with self.lock:
            self.stats = json.load(fil)

    def load_rules(self, rules_file):
        with self.lock:
            self.rules = json.load(rules_file)

    def save_json(self, filepath):
        with open(filepath, "w") as fo:
            json.dump(self.stats, fo, indent=4, sort_keys=True)

    def should_ignore(self, filepath):
        # remove ignored items from failure
        ignored_items = self.rules.get("tests.ignore", [])
        # check if ignored
        for i in ignored_items:
            if i in filepath:
                return True

        return False

    def should_skip(self, filepath):
        skipped_items = self.rules.get("tests.skip", [])
        for s in skipped_items:
            if s in filepath:
                return True

        return False

    def print_fails(self, fail_count=5, output=None, verbose=True):
        if output is None:
            output = sys.stderr

        # Do a dictionary comprehension to filter for keys that
        # start with "output.", but ignore the success case, since
        # we only want the failures here
        out_stats = {
            k: v
            for k, v in self.stats.items()
            if k.startswith("output.") and k != "output.success"
        }

        # Sort dictionary by length of key
        top_items = sorted(out_stats.items(), reverse=True, key=lambda x: len(x[1]))

        # Output the top fail_count items
        for k, v in top_items[:fail_count]:
            k = k.replace("output.", "")
            output.write(f"`{k}`: `{len(v)}` failures\n")
            if verbose:
                for test_fail in v:
                    output.write(f"    {test_fail}\n")



        ign_outputs = {"outputignore_success": None, "outputignore_fail": None}
        ign_count = 0
        for ign_type in ign_outputs.keys():
            ign_entry = self.stats.get(ign_type, [])
            ign_count += len(ign_entry)
            ign_outputs[ign_type] = (len(ign_entry), ign_entry)

        if ign_count > 0:
            output.write(f"Ignored {ign_count} tests.\n")
            ign_pass_count, ign_pass_list = ign_outputs["outputignore_success"]
            if ign_pass_count > 0:
                output.write(f"    {ign_pass_count} ignored tests are passing! They are:\n")
                if verbose:
                    for ign_pass_test in ign_pass_list:
                        output.write(f"    {ign_pass_test}\n")
                else:
                    output.write("    [set verbose=True to see test list]\n")

            else:
                output.write("    All ignored tests failed\n")

    def get_fail_count(self):
        success_runs = len(self.stats.get("output.success", []))
        timed_out_runs = self.stats.get("program_timeouts", 0)
        program_runs = self.stats.get("program_runs", 0)
        ignored_outputs = len(self.stats.get("outputignore_success", []))
        ignored_outputs += len(self.stats.get("outputignore_fail", []))
        return (program_runs - ignored_outputs) - (success_runs + timed_out_runs)

    def print_stats(self, output=None):
        # emit start/end time
        # emit %success

        if output is None:
            output = sys.stderr

        if "start_time" in self.stats and "end_time" in self.stats:
            start_time = datetime.fromisoformat(self.stats["start_time"])
            end_time = datetime.fromisoformat(self.stats["end_time"])

            time_diff = end_time - start_time
            secs = time_diff.total_seconds()

            output.write(f"Run took {time_diff}\n")

            if "program_runs" in self.stats:
                runs = self.stats["program_runs"]
                runs_per_sec = runs / secs
                output.write(f"Speed of {runs_per_sec:.2f} runs/sec\n")

        success_runs = self.stats.get("output.success", [])
        program_runs = self.stats.get("program_runs", 0)

        success_items = len(success_runs)
        success_percent = 100.0 * success_items / program_runs

        if program_runs != 0:
            output.write(f"Success Metrics: [{success_items}/{program_runs}]\n")
            output.write(f"Success Percentage: [{success_percent:.2f}%]\n")

        if output is not None:
            output.flush()

    def print_json(self):
        self.save_json("/dev/stdout")

    def add_stat(self, key, value):
        with self.lock:
            self.stats.setdefault(key, []).append(value)

    def inc_stat(self, key):
        # probably needs a lock but #YOLO
        item = self.stats.get(key, 0)
        self.stats[key] = item + 1

    def set_stat(self, key, value):
        # probably needs a lock but #YOLO
        self.stats[key] = value
