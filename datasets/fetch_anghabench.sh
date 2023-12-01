#!/usr/bin/env bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

set -euo pipefail

CLANG=clang-14
RUN_SIZE=1k
FETCH_BITCODE="no"
FETCH_BINARIES="no"

mapfile -t ARCHES < ${DIR}/architectures.txt

function Help
{
  echo "Fetch AnghaBench pre-compiled datasets"
  echo ""
  echo "Options:"
  echo "  --clang           Which version of clang built the dataset [${CLANG}]"
  echo "  --run-size        How many binaries (choices: 50, 1k, 1m)"
  echo "  --bitcode         Fetch bitcode"
  echo "  --binaries        Fetch binaries (ELF .o)"
  echo "  -h --help         Print help."
}

# supported sizes are currently "1k", "1m" and empty ("")
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

		# Which clang built these
		--clang)
			CLANG=clang-${2,,}
			shift # past argument
		;;

		# How large of a run to get
		--run-size)
			RUN_SIZE=${2,,}
			shift # past argument
		;;

		# Fetch bitcode?
		--bitcode)
			FETCH_BITCODE="yes"
		;;

		# Fetch binaries? 
		--binaries)
			FETCH_BINARIES="yes"
		;;

		*)
			# unknown option
			echo "[x] Unknown option: ${key}"
			exit 1
		;;
	esac

	shift # past argument or value
done

echo "[+] Fetching for clang: ${CLANG}"
echo "[+] Run size: ${RUN_SIZE}"
echo "[+] Fetch bitcode: ${FETCH_BITCODE}"
echo "[+] Fetch binaries: ${FETCH_BINARIES}"

if [[ "${FETCH_BITCODE}" = "no" && "${FETCH_BINARIES}" = "no" ]]
then
	echo "[!] Please specify --bitcode or --binaries"
	exit 1
fi

if [[ "${FETCH_BITCODE}" = "yes" ]]
then
  for arch in "${ARCHES[@]}"
  do
    bcfile=${RUN_SIZE}.bitcode.${CLANG}.${arch}.tar.xz
    url_to_get=https://anghabench-files-public.nyc3.digitaloceanspaces.com/${CLANG}/${bcfile}
    echo "Fetching [${url_to_get}]"
    curl -LO ${url_to_get}
  done
fi

if [[ "${FETCH_BINARIES}" = "yes" ]]
then
  for arch in "${ARCHES[@]}"
  do
    bcfile=${RUN_SIZE}.binaries.${CLANG}.${arch}.tar.xz
    url_to_get=https://anghabench-files-public.nyc3.digitaloceanspaces.com/${CLANG}/${bcfile}
    echo "Fetching [${url_to_get}]"
    curl -LO ${url_to_get}
  done
fi

