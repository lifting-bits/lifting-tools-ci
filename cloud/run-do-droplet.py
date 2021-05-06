#!/usr/bin/env python3

import requests
import os
import json
from datetime import datetime
import argparse
import sys

def replace_vars(vars, script):
    for k,v in vars.items():
        script = script.replace(f"__{k}__", v)
    
    return script


def make_do_droplet(token, droplet_info):
    url = "https://api.digitalocean.com/v2/droplets" 
    resp = requests.post(
        url, data = json.dumps(droplet_info),
        headers = {'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'})

    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"Could not make DO Droplet: {str(resp)}: {resp.content}")
    else:
        sys.stdout.write("Successfully created new droplet!\n")

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--name", default=f"ci-run-{datetime.today().strftime('%Y-%m-%d')}", help="Name to identify this instance")
    parser.add_argument(
        "--token", default=os.environ.get("DO_TOKEN", ""), help="DO Access Token")

    parser.add_argument("--env-vars", default="", help="A list of values in the shape of: [var=value,var2=value2,...]. Text replace __var__ with value in `script`.")
    parser.add_argument("--script", required=True, help="Bash script to run on droplet start")
    parser.add_argument("--region", default="nyc3", help="DO region where to create droplet")
    parser.add_argument("--image", default="ubuntu-20-04-x64", help="OS Image to run on the droplet")

    parser.add_argument(
        "--instance",
        default="c-32",
        help="Instance type to create"
    )

    args = parser.parse_args()

    script_dir =  os.path.dirname(__file__)
    
    do_header = os.path.join(script_dir, "do", "header.sh")
    do_trailer = os.path.join(script_dir, "do", "trailer.sh")
    do_data = os.path.realpath(args.script)

    for pth in (do_header, do_trailer, do_data):
        if not os.path.exists(pth):
            sys.stderr.write(f"Could not open startup script [{pth}]\n")
            sys.exit(1)
   

    if not args.token:
        sys.stderr.write("Please set a DO token in the DO_TOKEN env var\n")
        sys.exit(1)

    if "SLACK_HOOK" not in os.environ:
        sys.stderr.write("Please set SLACK_HOOK env var\n")
        sys.exit(1)

    sys.stdout.write(f"Creating DO droplet [{args.name}] of type [{args.instance}]\n")


    VARS_TO_FIX = {
        "DO_TOKEN": args.token,
        "SLACK_HOOK": os.environ["SLACK_HOOK"],
        "RUN_NAME": args.name,
    }

    if args.env_vars:
        parts = args.env_vars.split(',')
        for prt in parts:
            p = prt.split('=')
            if len(p) > 1:
                VARS_TO_FIX[p[0]] = p[1]

    script_header = replace_vars(VARS_TO_FIX, open(do_header, 'r').read())
    do_script = replace_vars(VARS_TO_FIX, open(do_data, 'r').read())
    script_trailer = replace_vars(VARS_TO_FIX, open(do_trailer, 'r').read())

    full_script = "\n".join([script_header, do_script, script_trailer])

    di = { 
        "name": args.name,
        "region": args.region,
        "size": args.instance,
        "image": args.image,
        "ssh_keys": None,
        "user_data": full_script,
        "tags": ["ci", "binary-lifting"],
    }

    make_do_droplet(args.token, di)
