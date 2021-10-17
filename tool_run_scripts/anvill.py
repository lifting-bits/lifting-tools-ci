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


log = logging.getLogger("anvill_test_suite")
log.addHandler(logging.StreamHandler())
#log.setLevel(logging.DEBUG)
log.setLevel(logging.INFO)


MYDIR = path.dirname(path.abspath(__file__))
MSG_HOOK = ""
VERSION = ""

# given some input bitocode, run it through anvill record outputs

class AnvillPythonCmd(ToolCmd):

    def make_tool_cmd(self):
        f = self.infile.stem
        jsonfile = f"{self.index}-{f}.json"
        self.tmpout = self.outdir.joinpath("work").joinpath(jsonfile)

        #python3 -m anvill --bin_in foo.elf --spec_out foo.json 
        log.debug(f"Setting tmpout to: {self.tmpout}")
        args = self.tool.split()
        args.extend([
            "--bin_in",
            str(self.infile),
            "--spec_out",
            str(self.tmpout),
            "--log_file",
            "/dev/stderr",
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

        input_name = pth.joinpath("input.elf")
        shutil.copyfile(self.infile, input_name)

        if self.rc == 0:
            output_name = pth.joinpath("output.json")
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

class AnvillDecompileCmd(ToolCmd):

    def make_tool_cmd(self):
        f = self.infile.stem
        bcfile = f"{self.index}-{f}.bc"
        self.tmpout = self.outdir.joinpath("work").joinpath(bcfile)

        #anvill-decompile-json-11.0 -spec <json file> -bc_out <bc_file>
        log.debug(f"Setting tmpout to: {self.tmpout}")
        args = self.tool.split()
        args.extend([
            "-spec",
            str(self.infile),
            "-bc_out",
            str(self.tmpout),
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

def run_anvill_python(anvill, output_dir, failonly, source_path, stats, input_and_idx):
    idx, input_file = input_and_idx
    cmd = AnvillPythonCmd(anvill, input_file, output_dir, source_path, idx, stats)

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

def run_anvill_decompile(anvill, output_dir, failonly, source_path, stats, input_and_idx):
    idx, input_file = input_and_idx
    cmd = AnvillDecompileCmd(anvill, input_file, output_dir, source_path, idx, stats)

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
        rt =  subprocess.run([cmd, "--version"], timeout=30, capture_output=True)
    except OSError as oe:
        log.error(f"Could not get anvill version: {oe}")
        sys.exit(1)
    except subprocess.CalledProcessError as cpe:
        log.error(f"Could not get anvill version: {cpe}")
        sys.exit(1)
    except subprocess.TimeoutExpired as tme:
        log.error(f"Could not get anvill version: timeout execption")
        sys.exit(1)

    return rt.stdout.decode("utf-8")


def anvill_python_main(args, source_path, dest_path):
    num_cpus = os.cpu_count()
    anvill_stats = Stats()

    if args.test_options:
        with open(args.test_options, "r") as rf:
            anvill_stats.load_rules(rf)

    # get all the bitcode
    log.info(f"Listing files in {str(source_path)}")
    sources = list(source_path.rglob("*.elf"))
    log.info(f"Found {len(sources)} ELF files")

    # load test to ignore
    anvill_stats.set_stat("start_time", str(datetime.now()))

    max_items_python = len(sources)

    # workspace for anvill-python
    apply_anvill_python = partial(run_anvill_python, args.anvill_python, dest_path, args.only_fails, source_path, anvill_stats)

    with ThreadPool(num_cpus) as p:
        with tqdm(total=max_items_python) as pbar:
            for _ in p.imap_unordered(apply_anvill_python, enumerate(sources)):
                pbar.update()

    anvill_stats.set_stat("end_time", str(datetime.now()))

    if args.dump_stats:
        outpath = dest_path.joinpath("stats.json")
        anvill_stats.save_json(outpath)

    if args.slack_notify:
        dump_via_slack(args, anvill_stats)

def anvill_decomp_main(args, source_path, dest_path):

    sources_decompile = list(source_path.rglob("*.json"))
    if sources_decompile:
        workdir_decompile = str(dest_path.joinpath("work"))
        log.debug(f"Making work dir [{workdir_decompile}]")
        os.makedirs(workdir_decompile, exist_ok=True)
    log.info(f"Found {len(sources_decompile)} JSON specs")

    num_cpus = os.cpu_count()
    anvill_stats = Stats()

    if args.test_options:
        with open(args.test_options, "r") as rf:
            anvill_stats.load_rules(rf)

    anvill_stats.set_stat("start_time", str(datetime.now()))

    max_items_decompile = len(sources_decompile)

    apply_anvill_decomp = partial(run_anvill_decompile, args.anvill_decompile, dest_path, args.only_fails, source_path_decompile, anvill_stats)

    with ThreadPool(num_cpus) as p:
        with tqdm(total=max_items_decompile) as pbar:
            for _ in p.imap_unordered(apply_anvill_decomp, enumerate(sources_decompile)):
                pbar.update()

    anvill_stats.set_stat("end_time", str(datetime.now()))

    if args.dump_stats:
        outpath = dest_path.joinpath("stats.json")
        anvill_stats.save_json(outpath)

    if args.slack_notify:
        dump_via_slack(args, anvill_stats)

def dump_via_slack(args, stats):
    MSG_HOOK = os.environ.get("SLACK_HOOK", None)

    if not MSG_HOOK:
        sys.stderr.write("Invalid webhook in SLACK_HOOK env var\n")
        sys.exit(1)

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
        stats.print_fails(fail_count=max_num_fails, output=fail_msg, verbose=False)
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
    parser.add_argument(
        "--anvill-python", default="python3 -m anvill", help="Which anvill python frontend to run"
    )
    parser.add_argument(
        "--anvill-decompile", default="anvill-decompile-json-11.0", help="Which anvill decompiler to run"
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

    parser.add_argument(
        "--test-options",
        default=None,
        help="A JSON file specifying tests to ignore or expect to fail")

    args = parser.parse_args()

    if args.test_options and not os.path.exists(args.test_options):
        sys.stderr.write(f"Test options file [{args.test_options}] was not found\n")
        sys.exit(1)

    test_anvill_args = args.anvill_python.split()
    test_anvill_args.append("-h")
    anvill_test = subprocess.run(test_anvill_args, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    if anvill_test.returncode != 0:
        sys.stderr.write(f"Could not find anvill command: {args.anvill_python}\n")
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
