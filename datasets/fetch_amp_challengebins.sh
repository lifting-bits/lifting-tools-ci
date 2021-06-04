#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

set -euo pipefail

function Help
{
  echo "Fetch pre-compiled AMP Challenge Binaries"
  echo ""
  echo "Options:"
  echo "  -h --help         Print help."
}

while [[ $# -gt 0 ]] ; do
	key="$1"

	case $key in

		-h)
			Help
			exit 0
		;;

		--help)
			Help
			exit 0
		;;

		*)
			# unknown option
			echo "[x] Unknown option: ${key}"
			exit 1
		;;
	esac

	shift # past argument or value
done

curl -LO https://tob-amp-share.nyc3.digitaloceanspaces.com/challenge-binaries-latest.tar.xz.gpg
gpg --no-tty --batch --pinentry-mode loopback --passphrase "${TOB_AMP_PASSPHRASE}" \
	-o challenge-binaries-latest.tar.xz \
	--decrypt challenge-binaries-latest.tar.xz.gpg
rm -rf challenge-binaries-latest.tar.xz.gpg