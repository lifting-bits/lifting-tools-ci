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

# given some input bitocode, run it through anvill record outputs

class AnvillCmd(ToolCmd):

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

        self.stats.add_stat(f"output.{out_path_name}", str(self.infile))

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

def run_anvill(anvill, output_dir, failonly, source_path, stats, input_and_idx):
    idx, input_file = input_and_idx
    cmd = AnvillCmd(anvill, input_file, output_dir, source_path, idx, stats)

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

    args = parser.parse_args()

    test_anvill_args = args.anvill_python.split()
    test_anvill_args.append("-h")
    anvill_test = subprocess.run(test_anvill_args, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    if anvill_test.returncode != 0:
        sys.stderr.write(f"Could not find anvill command: {args.anvill_python}\n")
        sys.exit(1)

    if args.slack_notify:
        msg_hook = os.environ.get("SLACK_HOOK", None)

        if not msg_hook:
            sys.stderr.write("Invalid webhook in SLACK_HOOK env var\n")
            sys.exit(1)

    version = get_anvill_version(args.anvill_decompile)
    log.info(f"Running against Anvill:\n{version}")

    source_path = Path(args.input_dir)
    dest_path = Path(args.output_dir)
    # get all the bitcode
    log.info(f"Listing files in {str(source_path)}")
    sources = list(source_path.rglob("*.elf"))
    log.info(f"Found {len(sources)} ELF files")

    if sources:
        workdir = str(dest_path.joinpath("work"))
        log.debug(f"Making work dir [{workdir}]")
        os.makedirs(workdir, exist_ok=True)

    
    num_cpus = os.cpu_count()
    max_items = len(sources)

    anvill_stats = Stats()
    anvill_stats.set_stat("start_time", str(datetime.now()))

    apply_anvill = partial(run_anvill, args.anvill_python, dest_path, args.only_fails, source_path, anvill_stats)


    #with Pool(processes=num_cpus) as p:
    with ThreadPool(num_cpus) as p:
        with tqdm(total=max_items) as pbar:
            for _ in p.imap_unordered(apply_anvill, enumerate(sources)):
                pbar.update()

    anvill_stats.set_stat("end_time", str(datetime.now()))

    if args.slack_notify:
        slack_msg = Slack(msg_hook)
        slack_msg.add_header(f"{args.run_name}")
        slack_msg.add_block(f"Anvill Version: ```{version}```")
        slack_msg.add_divider()

        with StringIO() as stat_msg:
            anvill_stats.print_stats(stat_msg)
            slack_msg.add_block(stat_msg.getvalue())

        slack_msg.add_divider()

        with StringIO() as fail_msg:
            max_num_fails = 10
            slack_msg.add_block(f"Top {max_num_fails}:")
            anvill_stats.print_fails(fail_count=max_num_fails, output=fail_msg)
            slack_msg.add_block(fail_msg.getvalue())
        
        slack_msg.post()
