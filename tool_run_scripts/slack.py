#!/usr/bin/env python3
import json
import requests
import sys
import argparse
import os

class Slack:
    def __init__(self, hook, footer=None):
        self.hook = hook
        self.footer = footer
        self.blocks = []

    def add_divider(self):
        div = {
            'type': 'divider',
        }
        self.blocks.append(div)

    def add_header(self, header_text, type="plain_text"):

        header = {
            'type': "header",
            'text': {
                'type': type,
                'text': header_text,
            }
        }

        self.blocks.append(header)

    def add_block(self, block_text, type="mrkdwn"):

        block = {
            'type': 'section',
            'text': {
                'type': type,
                'text': block_text,
            }
        }

        self.blocks.append(block)

    def post(self):

        msg_d = {
            'blocks': self.blocks
        }
        # for debugging
        #sys.stdout.write(json.dumps(msg_d, indent=4, sort_keys=True))
        resp = requests.post(
            self.hook, data = json.dumps(msg_d),
            headers = {'Content-Type': 'application/json'})

        if resp.status_code != 200:
            raise RuntimeError(f"Could not post slack messsage: [{resp.status_code}]: {resp.text}")

if "__main__" == __name__:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--slack-hook",
        default=os.environ.get("SLACK_HOOK", ""),
        help="The slack hook to message. Defaults to SLACK_HOOK env var"
    )

    parser.add_argument(
        "--header",
        default="",
        help="Header of the slack message, if any"
    )

    parser.add_argument("--msg", help="The message(s) to send", action="extend", nargs="+", type=str)

    args = parser.parse_args()

    msg_hook = args.slack_hook
    if "" == msg_hook:
        sys.stderr.write("Could not find a 'SLACK_HOOK' environment variable\n")
        sys.exit(1)
   
    slack_msg = Slack(msg_hook)

    if args.header != "":
        slack_msg.add_header(args.header)

    for message in args.msg:
        slack_msg.add_block(message)

    slack_msg.post()

