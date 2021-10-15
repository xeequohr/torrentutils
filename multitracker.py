#!/usr/bin/python3 -O

from argparse import ArgumentParser
from configparser import ConfigParser
from math import log2
import os
import shlex
from subprocess import run, DEVNULL
import sys

def parse_cli():
	parser = ArgumentParser(description="Make torrent files for multiple trackers")
	parser.add_argument("--opt-piece-count", "-o", type=int, default=1536, metavar="N", help="Set the optimal number of pieces to N (default: 1536)")
	parser.add_argument("--trackers", "-t", nargs="+", metavar="SECTION", help="Tracker section defined in ~/.torrentutils/trackers (default: all)")
	parser.add_argument("filename", nargs="+", help="Target file or directory")
	return parser.parse_args()

def get_size(filename):
	size = 0
	if os.path.isdir(filename):
		for dirpath, dirnames, filenames in os.walk(filename):
			for f in filenames:
				size += os.stat(os.path.join(dirpath, f)).st_size
	else:
		size += os.stat(filename).st_size
	return size

def main(args):
	config = ConfigParser()
	config.read(os.path.join(os.environ["HOME"], ".torrentutils", "trackers" ))
	trackers = args.trackers or config.sections()
	returncode = 0

	for filename in args.filename:
		size = get_size(filename)
		os.umask(0o0266)
		piece_size = str(round(log2(size / args.opt_piece_count)))
		for tracker in trackers:
			torrentfile = ".".join(( filename, tracker, "torrent" ))
			cmd = [ "mktorrent", "-d"]
			if config[tracker]["dht"] == "disabled":
				cmd += [ "-p", "-s", tracker ]
			cmd += [ "-o", torrentfile, "-l", piece_size ]
			tiers = sorted([
				k.split(".")[2]
				for k in config[tracker].keys()
				if k.startswith("announce.tier.")
			], key=int)
			for tier in tiers:
				urls = sorted([ k.split(".")[4] for k in config[tracker] if k.startswith("announce.tier." + tier + ".url.") ], key=int)
				urls = [ config[tracker]["announce.tier." + tier + ".url." + url] for url in urls ]
				cmd.append("-a")
				cmd.append(",".join(urls))
			cmd.append(filename)
			print(*[ shlex.quote(arg) for arg in cmd ])
			result = run(cmd, stdin=DEVNULL, stdout=DEVNULL).returncode
			if returncode == 0 and result != 0:
				returncode = result

	return returncode

if __name__ == "__main__":
	sys.exit(main(parse_cli()))
