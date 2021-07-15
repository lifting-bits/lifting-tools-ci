#!/usr/bin/env python3
import argparse
import sys
import shutil
import logging
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
from datetime import datetime
from io import StringIO
import subprocess

log = logging.getLogger("recompile_test")
log.addHandler(logging.StreamHandler())
#log.setLevel(logging.DEBUG)
log.setLevel(logging.INFO)


MYDIR = path.dirname(path.abspath(__file__))


def get_clang_version(cmd):
    try:
        rt =  subprocess.run([cmd, "--version"], timeout=30, capture_output=True)
    except OSError as oe:
        log.error(f"Could not get clang version: {oe}")
        sys.exit(1)
    except subprocess.CalledProcessError as cpe:
        log.error(f"Could not get clang version: {cpe}")
        sys.exit(1)
    except subprocess.TimeoutExpired as tme:
        log.error(f"Could not get clang version: timeout execption")
        sys.exit(1)

    return rt.stdout.decode("utf-8")

class ClangCmd(ToolCmd):

    def make_tool_cmd(self):
        f = self.infile.stem
        ofile = f"{self.index}-{f}.o"
        self.tmpout = self.outdir.joinpath("work").joinpath(ofile)

        # clang -c -o /output/dir/foo.o /iput/dir/foo.c
        log.debug(f"Setting tmpout to: {self.tmpout}")
        args = [
            self.tool,
            "-c",
            "-o",
            str(self.tmpout),
            str(self.infile),
        ]
        return args

    def save(self):

        if self.rc is None:
            raise RuntimeError("Return code never set")

        out_path_name = self.get_output_path()
        pth = self.outdir.joinpath(out_path_name)
        pth = pth.joinpath(self.infile.relative_to(self.source_base))

        out_key = f"output.{out_path_name}"
        if self.stats.should_ignore(str(self.infile)):
            if self.rc == 0:
                out_key = "outputignore_success"
            else:
                out_key = "outputignore_fail"

        self.stats.add_stat(out_key, str(self.infile))

        log.debug(f"Making dir: {pth}")
        os.makedirs(pth, exist_ok=True)

        input_name = pth.joinpath("input.c")
        shutil.copyfile(self.infile, input_name)


        if self.rc == 0:
            output_name = pth.joinpath("output.o")
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

def run_clang(clang, output_dir, failonly, source_path, stats, input_and_idx):
    idx, input_file = input_and_idx
    cmd = ClangCmd(clang, input_file, output_dir, source_path, idx, stats)

    retcode = cmd.run()
    log.debug(f"Clang run returned {retcode}")

    if not failonly:
        cmd.save()
    elif failonly and retcode != 0:
        log.debug("Saving clang failure case")
        cmd.save()
    else:
        log.debug("Successful clang invocation not saved due to --only-fails=True")

    return cmd

if __name__ == "__main__":

    # recompile.py
    #   --input-dir input_dir
    #   --output-dir output_dir
    #   --only-fails
    #   --slack-notify

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--clang", default="clang", help="Which clang to run"
    )
    parser.add_argument(
        "--input-dir",
        default=f"{MYDIR}/../source",
        help="where to look for source files",
    )
    parser.add_argument(
        "--output-dir",
        default=f"{MYDIR}/../results/recompile",
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
        help="Notify slack. SLACK_HOOK env var for the webhook",
    )
    parser.add_argument(
        "--run-name",
        default="Recompile Batch Run",
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

    if shutil.which(args.clang) is None:
        sys.stderr.write(f"Could not find clang command: {args.clang}\n")
        sys.exit(1)

    if args.test_options and not os.path.exists(args.test_options):
        sys.stderr.write(f"Test options file [{args.test_options}] was not found\n")
        sys.exit(1)

    if args.slack_notify:
        msg_hook = os.environ.get("SLACK_HOOK", None)

        if not msg_hook:
            sys.stderr.write("Invalid webhook in SLACK_HOOK env var\n")
            sys.exit(1)

    version = get_clang_version(args.clang)
    log.info(f"Running against Clang:\n{version}")

    source_path = Path(args.input_dir)
    dest_path = Path(args.output_dir)
    # get all the C 
    log.info(f"Listing files in {str(source_path)}")
    sources = list(source_path.rglob("*.c"))
    sources = list(filter(lambda x: os.path.isfile(x), sources))
    log.info(f"Found {len(sources)} C files")

    if sources:
        workdir = str(dest_path.joinpath("work"))
        log.debug(f"Making work dir [{workdir}]")
        os.makedirs(workdir, exist_ok=True)

    
    num_cpus = os.cpu_count()
    max_items = len(sources)
    recompile_stats = Stats()

    if args.test_options:
        with open(args.test_options, "r") as rf:
            recompile_stats.load_rules(rf)

    recompile_stats.set_stat("start_time", str(datetime.now()))
    apply_clang = partial(run_clang, args.clang, dest_path, args.only_fails, source_path, recompile_stats)

    #with Pool(processes=num_cpus) as p:
    with ThreadPool(num_cpus) as p:
        with tqdm(total=max_items) as pbar:
            for _ in p.imap_unordered(apply_clang, enumerate(sources)):
                pbar.update()

    recompile_stats.set_stat("end_time", str(datetime.now()))

    max_num_fails = 10
    outpath = dest_path.joinpath("stats.json")
    recompile_stats.save_json(outpath)
    recompile_stats.print_stats()
    recompile_stats.print_fails(fail_count=max_num_fails)

    # validity of msg_hook checked earlier
    if args.slack_notify:
        slack_msg = Slack(msg_hook)
        slack_msg.add_header(f"{args.run_name}")
        slack_msg.add_block(f"Clang Version: ```{version}```")
        slack_msg.add_divider()

        with StringIO() as stat_msg:
            recompile_stats.print_stats(stat_msg)
            slack_msg.add_block(stat_msg.getvalue())

        slack_msg.add_divider()

        with StringIO() as fail_msg:
            slack_msg.add_block(f"Top {max_num_fails}:")
            # verbose is set to False here to avoid slack messages
            # that are too long for Slack
            recompile_stats.print_fails(fail_count=max_num_fails, output=fail_msg, verbose=False)
            slack_msg.add_block(fail_msg.getvalue())
        
        slack_msg.post()
