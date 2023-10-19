#!/usr/bin/env python3
import argparse
import sys
import shutil
import logging
import subprocess
from os import path
import os
from pathlib import Path
from tqdm import tqdm
from multiprocessing import Pool
from multiprocessing.pool import ThreadPool
from functools import partial
from toolcmd import ToolCmd
from stats import Stats
from slack import Slack
from io import StringIO
from datetime import datetime
import json
from collections import Counter
from threading import Lock

log = logging.getLogger("anvill_test_suite")
log.addHandler(logging.StreamHandler())
log.setLevel(logging.DEBUG)
#log.setLevel(logging.INFO)


MYDIR = path.dirname(path.abspath(__file__))
MSG_HOOK = ""
VERSION = ""

# given some input bitocode, run it through anvill record outputs


class AnvillGhidraCmd(ToolCmd):

    def __init__(self, tool, infile, outdir, source_base, index, stats, language_overrides):
        self.lang_overrides = language_overrides
        super().__init__(tool, infile, outdir, source_base, index, stats)

    def make_tool_cmd(self):
        f = self.infile.stem
        fullname = self.infile.name

        jsonfile = f"{self.index}-{f}.pb"
        self.tmpout = self.outdir.joinpath("work").joinpath(jsonfile)

        # python3 -m anvill --bin_in foo.elf --spec_out foo.json
        log.debug(f"Setting tmpout to: {self.tmpout}")
        args = [os.path.join(self.tool, "support", "analyzeHeadless")]

        args.extend([
            "/tmp",
            f"dummy_ghidra_proj{self.index}-{f}",
            "-readOnly",
            "-deleteProject"]
            + (["-processor", self.lang_overrides[fullname]] if fullname in self.lang_overrides else [])
            +["-import",
            str(self.infile),
            "-postScript",
            "FixGlobalRegister",
            "-postScript",
            "anvillHeadlessExportScript",
            str(self.tmpout),
        ])
        return args

    def make_env(self) -> dict[str, str]:
        # glibc preallocates arenas per thread to improve allocation performance. For machines with
        # a high core count, this can dwarf the amount of memory used by the JVM's heap or
        # internals. This is a problem when running on environments like GHA with a hard memory
        # constraint as jobs will be repeatedly killed. We should set `MALLOC_ARENA_MAX` to limit
        # the number of arenas that glibc is allowed to allocate.
        #
        #   https://thehftguy.com/2020/05/21/major-bug-in-glibc-is-killing-applications-with-a-memory-limit/
        return {"MALLOC_ARENA_MAX": "4"}

    def save(self):
        if self.rc is None:
            raise RuntimeError("Return code never set")

        out_path_name = self.get_output_path()
        pth = self.outdir.joinpath(out_path_name)
        pth = pth.joinpath(self.infile.relative_to(self.source_base))

        log.debug(f"Making dir: {pth}")
        os.makedirs(pth, exist_ok=True)

        out_key = f"output.{out_path_name}"
        if self.stats.should_ignore(str(self.infile)):
            if self.rc == 0:
                out_key = "outputignore_success"
            else:
                out_key = "outputignore_fail"

        self.stats.add_stat(out_key, str(self.infile))

        input_name = pth.joinpath("input.elf")
        shutil.copyfile(self.infile, input_name)

        if self.rc == 0:
            output_name = pth.joinpath("output.pb")
            log.debug(f"Copying {self.tmpout} to {output_name}")
            shutil.copyfile(self.tmpout, output_name)

        dumpout = pth.joinpath("stdout")
        with open(dumpout, "w") as out:
            if type(self.out) is bytes:
                out.buffer.write(self.out)
            else:
                out.write(str(self.out))

        dumperr = pth.joinpath("stderr")
        with open(dumperr, "w") as err:
            if type(self.err) is bytes:
                err.buffer.write(self.err)
            else:
                err.write(str(self.err))

        repro = pth.joinpath("repro.sh")
        with open(repro, 'w') as reprofile:
            reprofile.write("#!/bin/sh\n")
            reprofile.write(" ".join(self.cmd))
            reprofile.write("\n")


class DecompileStats:
    def __init__(self) -> None:
        self.lock = Lock()
        self.stat_dict = Counter()

    def add_stats(self, file_path):
        with self.lock:
            with open(file_path, 'r') as f:
                nd = json.load(f)
                self.stat_dict.update(nd)

    def dump(self, outpath):
        with self.lock:
            with open(outpath, 'w') as f:
                json.dump(self.stat_dict, f)


class AnvillDecompileCmd(ToolCmd):

    def __init__(self, tool, infile, outdir, source_base, index, stats, decomp_stats):
        super().__init__(tool, infile, outdir, source_base, index, stats)
        self.decomp_stats = decomp_stats

    def make_tool_cmd(self):
        f = self.infile.stem
        bcfile = f"{self.index}-{f}.bc"
        self.work_dir = self.outdir.joinpath("work")
        self.stats_file = self.work_dir.joinpath(f"{self.index}-{f}.stats")
        self.tmpout = self.work_dir.joinpath(bcfile)

        # anvill-decompile-spec <json file> -bc_out <bc_file>
        log.debug(f"Setting tmpout to: {self.tmpout}")
        args = self.tool.split()
        args.extend([
            "-spec",
            str(self.infile),
            "-bc_out",
            str(self.tmpout),
            "-stats_out",
            str(self.stats_file),
            "-logtostderr",
        ])

        return args

    def save(self):
        if self.rc is None:
            raise RuntimeError("Return code never set")

        out_path_name = self.get_output_path()
        pth = self.outdir.joinpath(out_path_name)
        pth = pth.joinpath(self.infile.relative_to(self.source_base))

        log.debug(f"Making dir: {pth}")
        os.makedirs(pth, exist_ok=True)

        out_key = f"output.{out_path_name}"
        if self.stats.should_ignore(str(self.infile)):
            if self.rc == 0:
                out_key = "outputignore_success"
            else:
                out_key = "outputignore_fail"

        self.stats.add_stat(out_key, str(self.infile))

        input_name = pth.joinpath("input.json")
        shutil.copyfile(self.infile, input_name)

        if self.rc == 0:
            output_name = pth.joinpath("output.bc")
            log.debug(f"Copying {self.tmpout} to {output_name}")
            shutil.copyfile(self.tmpout, output_name)
            log.debug(f"Aggregating stats file {self.stats_file}")
            self.decomp_stats.add_stats(self.stats_file)

        dumpout = pth.joinpath("stdout")
        with open(dumpout, "w") as out:
            if type(self.out) is bytes:
                out.buffer.write(self.out)
            else:
                out.write(str(self.out))

        dumperr = pth.joinpath("stderr")
        with open(dumperr, "w") as err:
            if type(self.err) is bytes:
                err.buffer.write(self.err)
            else:
                err.write(str(self.err))

        repro = pth.joinpath("repro.sh")
        with open(repro, 'w') as reprofile:
            reprofile.write("#!/bin/sh\n")
            reprofile.write(" ".join(self.cmd))
            reprofile.write("\n")

def initialize_script(ghidra_dir, name):
    try:
        args = [os.path.join(ghidra_dir, "support", "analyzeHeadless")]
        args.extend([
            "/tmp",
            "dummy_ghidra_proj_init",
            "-readOnly",
            "-deleteProject",
            "-preScript",
            name
        ])

        subprocess.run(args=args)
    except OSError as oe:
        log.error(f"Could not initialize ghidra: {oe}")
        sys.exit(1)
    except subprocess.CalledProcessError as cpe:
        log.error(f"Could not initialize: {cpe}")
        sys.exit(1)
    except subprocess.TimeoutExpired as tme:
        log.error(f"Could not initialize ghidra: timeout exception")
        sys.exit(1)

# Run the script with no input to trigger script compilation so it gets saved in the cache
def initialize_ghidra_cache(ghidra_dir):
    initialize_script(ghidra_dir, "anvillHeadlessExportScript")
    initialize_script(ghidra_dir, "FixGlobalRegister")

def run_anvill_ghidra(ghidra_dir, output_dir, failonly, source_path, stats, language_id_overrides, input_and_idx):
    idx, input_file = input_and_idx
    cmd = AnvillGhidraCmd(ghidra_dir, input_file, output_dir,
                          source_path, idx, stats, language_id_overrides)

    retcode = cmd.run()
    log.debug(f"Anvill run returned {retcode}")

    if not failonly:
        cmd.save()
    elif failonly and retcode != 0:
        log.debug("Saving anvill failure case")
        cmd.save()
    else:
        log.debug("Successful anvill invocation not saved due to --only-fails=True")

    return cmd


def run_anvill_decompile(anvill, output_dir, failonly, source_path, stats, decomp_stats, input_and_idx):
    idx, input_file = input_and_idx
    cmd = AnvillDecompileCmd(
        anvill, input_file, output_dir, source_path, idx, stats, decomp_stats)

    retcode = cmd.run()
    log.debug(f"Anvill Decompile run returned {retcode}")

    if not failonly:
        cmd.save()
    elif failonly and retcode != 0:
        log.debug("Saving anvill failure case")
        cmd.save()
    else:
        log.debug("Successful anvill invocation not saved due to --only-fails=True")

    return cmd


def get_anvill_version(cmd):
    try:
        rt = subprocess.run([cmd, "--version"],
                            timeout=30, capture_output=True)
    except OSError as oe:
        log.error(f"Could not get anvill version: {oe}")
        sys.exit(1)
    except subprocess.CalledProcessError as cpe:
        log.error(f"Could not get anvill version: {cpe}")
        sys.exit(1)
    except subprocess.TimeoutExpired as tme:
        log.error(f"Could not get anvill version: timeout exception")
        sys.exit(1)

    return rt.stdout.decode("utf-8")


def anvill_python_main(args, source_path, dest_path):
    anvill_stats = Stats()


    language_id_overrides = {}

    if args.test_options:
        with open(args.test_options, "r") as rf:
            anvill_stats.load_rules(rf)
            if "language_id_overrides" in anvill_stats.rules:
                language_id_overrides = anvill_stats.rules['language_id_overrides']

    # get all the bitcode
    log.info(f"Listing files in {str(source_path)}")
    # Filter for files that are executable
    sources = [source for source in source_path.rglob("*") if source.is_file() and os.access(source, os.X_OK) and not source.name.startswith(".")]

    # Add objects to source list. This is required for AnghaBench.
    sources.extend(list(source_path.rglob("*.o")))

    log.info(f"Found {len(sources)} Executable files")

    # load test to ignore
    anvill_stats.set_stat("start_time", str(datetime.now()))

    max_items_python = len(sources)

    # initialize ghidra cache to pre-compile the script
    initialize_ghidra_cache(os.path.expanduser(args.ghidra_install_dir))

    # workspace for anvill-python
    apply_anvill_ghidra = partial(
        run_anvill_ghidra, os.path.expanduser(args.ghidra_install_dir), dest_path, args.only_fails, source_path, anvill_stats, language_id_overrides)

    with ThreadPool(args.jobs) as p:
        with tqdm(total=max_items_python) as pbar:
            for _ in p.imap_unordered(apply_anvill_ghidra, enumerate(sources)):
                pbar.update()

    anvill_stats.set_stat("end_time", str(datetime.now()))

    if args.dump_stats:
        outpath = dest_path.joinpath("stats.json")
        anvill_stats.save_json(outpath)

    if args.slack_notify:
        dump_via_slack(args, anvill_stats)


def anvill_decomp_main(args, source_path, dest_path):

    sources_decompile = list(source_path.rglob("*.pb"))
    if sources_decompile:
        workdir_decompile = str(dest_path.joinpath("work"))
        log.debug(f"Making work dir [{workdir_decompile}]")
        os.makedirs(workdir_decompile, exist_ok=True)
    log.info(f"Found {len(sources_decompile)} PB specs")

    anvill_stats = Stats()

    decompilation_stats = DecompileStats()

    if args.test_options:
        with open(args.test_options, "r") as rf:
            anvill_stats.load_rules(rf)

    anvill_stats.set_stat("start_time", str(datetime.now()))

    max_items_decompile = len(sources_decompile)

    apply_anvill_decomp = partial(run_anvill_decompile, args.anvill_decompile,
                                  dest_path, args.only_fails, source_path_decompile, anvill_stats, decompilation_stats)

    with ThreadPool(args.jobs) as p:
        with tqdm(total=max_items_decompile) as pbar:
            for _ in p.imap_unordered(apply_anvill_decomp, enumerate(sources_decompile)):
                pbar.update()

    anvill_stats.set_stat("end_time", str(datetime.now()))

    if args.dump_benchmark:
        outpath = dest_path.joinpath("decompile_stats.json")
        decompilation_stats.dump(outpath)

    if args.dump_stats:
        outpath = dest_path.joinpath("stats.json")
        anvill_stats.save_json(outpath)

    if args.slack_notify:
        dump_via_slack(args, anvill_stats)


def dump_via_slack(args, stats):
    slack_msg = Slack(MSG_HOOK)
    slack_msg.add_header(f"{args.run_name}")
    slack_msg.add_block(f"Anvill Version: ```{VERSION}```")
    slack_msg.add_divider()

    with StringIO() as stat_msg:
        stats.print_stats(stat_msg)
        slack_msg.add_block(stat_msg.getvalue())

    slack_msg.add_divider()

    with StringIO() as fail_msg:
        max_num_fails = 10
        slack_msg.add_block(f"Top {max_num_fails}:")
        # verbose is set to False to prevent overly long Slack messages
        stats.print_fails(fail_count=max_num_fails,
                          output=fail_msg, verbose=False)
        fail_output = fail_msg.getvalue()
        if fail_output:
            slack_msg.add_block(fail_output)
        else:
            slack_msg.add_block("<None>")

    slack_msg.post()


if __name__ == "__main__":

    # anvill.py
    #   --input-dir input_dir
    #   --output-dir output_dir
    #   --only-fails
    #   --slack-notify

    parser = argparse.ArgumentParser()

    parser.add_argument("--ghidra-install-dir", required=True, help="where to find ghidra for headless runs")

    parser.add_argument(
        "--anvill-decompile", default="anvill-decompile-spec", help="Which anvill decompiler to run"
    )
    parser.add_argument(
        "--input-dir",
        default=f"{MYDIR}/../compiled/binaries",
        help="where to look for binary inputs"
    )
    parser.add_argument(
        "--output-dir",
        default=f"{MYDIR}/../results/anvill",
        help="where to put results",
    )
    parser.add_argument(
        "--only-fails",
        default=False,
        action="store_true",
        help="Only output failing cases",
    )
    parser.add_argument(
        "--slack-notify",
        default=False,
        action="store_true",
        help="Notify slack about stats",
    )

    parser.add_argument(
        "--run-name",
        default="Anvill Batch Run",
        help="A name to identify this batch run"
    )

    parser.add_argument(
        "--dump-stats",
        default=False,
        action="store_true",
        help="Output a stats.json in output directory with run statistics")

    parser.add_argument("--dump-benchmark", default=False,
                        action="store_true", help="dump aggregated benchmark statistics")

    parser.add_argument(
        "--test-options",
        default=None,
        help="A JSON file specifying tests to ignore or expect to fail")

    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=os.cpu_count(),
        help="The number of jobs that can run concurrently; defaults to the system's CPU count")

    args = parser.parse_args()

    if args.test_options and not os.path.exists(args.test_options):
        sys.stderr.write(
            f"Test options file [{args.test_options}] was not found\n")
        sys.exit(1)

    if args.slack_notify:
        MSG_HOOK = os.environ.get("SLACK_HOOK", None)

        if not MSG_HOOK:
            sys.stderr.write("Invalid webhook in SLACK_HOOK env var\n")
            sys.exit(1)

    VERSION = get_anvill_version(args.anvill_decompile)
    log.info(f"Running against Anvill:\n{VERSION}")

    source_path = Path(args.input_dir)
    dest_path = Path(args.output_dir)

    python_dest_path = dest_path.joinpath("python")
    # make workdir and subdirs
    os.makedirs(python_dest_path.joinpath("work"), exist_ok=True)

    anvill_python_main(args, source_path, python_dest_path)

    source_path_decompile = python_dest_path.joinpath("success")
    dest_path_decompile = dest_path.joinpath("decompile")
    # make workdir and subdirs
    os.makedirs(dest_path_decompile.joinpath("work"), exist_ok=True)

    anvill_decomp_main(args, source_path_decompile, dest_path_decompile)
