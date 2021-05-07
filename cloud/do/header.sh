#!/bin/bash
# This is a header appended to scripts running on DigitalOcean droplets
# to ensure they self-destruct when done or on error

#DO Bearer Token
export DO_TOKEN=__DO_TOKEN__

function exit_hook {
  DROPLET_ID=$(curl -s http://169.254.169.254/metadata/v1/id)

  curl -X DELETE \
    -H "X-Dangerous: true" \
    -H "Authorization: Bearer ${DO_TOKEN}" \
    "https://api.digitalocean.com/v2/droplets/${DROPLET_ID}/destroy_with_associated_resources/dangerous" 
}

# always kill self on exit
trap exit_hook EXIT

# pretty much required
export SLACK_HOOK=__SLACK_HOOK__
export RUN_NAME="__RUN_NAME__"
