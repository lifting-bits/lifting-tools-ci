import json
import requests
import sys

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