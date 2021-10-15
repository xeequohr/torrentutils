#!/usr/bin/python3 -O
# vim: noexpandtab, tabstop=4, number
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from glob import glob
import json
from math import ceil, floor, log, sqrt
import os
import shlex
from subprocess import run, DEVNULL, PIPE, CalledProcessError
import sys

PLAYLIST_FILE = "playlist.txt"
CLIPS_FILE = "clips.gif"
LABEL_FILE = "label.png"
FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_SIZE = 16
FONT_COLOR = "white"
FONT_BACKGROUND = "black"
MIN_DEPTH = 96
MAX_DEPTH = 256
MIN_FRAME_RATE = 12
MAX_FRAME_RATE = 50
MIN_TIME_DELTA = 10

class VideoMetadata(object):
	def __init__(self, filename):
		self.filename = filename
		self.text = ffprobe_text(filename)
		metadata = ffprobe_json(filename)
		self.metadata = metadata
		video_streams = [ stream for stream in metadata["streams"] if stream["codec_type"] == "video" ]
		if not video_streams:
			raise Exception("No video stream found in " + filename)
		default_video_streams = [ stream for stream in video_streams if stream["disposition"]["default"] == 1 ]
		self.video_stream = default_video_streams[0] if default_video_streams else video_streams[0]
		self.format = metadata["format"]

	@property
	def width(self): return int(self.video_stream["width"])
	
	@property
	def height(self): return int(self.video_stream["height"])

	@property
	def length(self): return float(self.video_stream.get("duration", self.format.get("duration")))

	@property
	def frame_rate_str(self): return str(self.video_stream["r_frame_rate"])

	@property
	def frame_rate_num(self): return int(self.frame_rate_str.split("/")[0])

	@property
	def frame_rate_den(self): return int(self.frame_rate_str.split("/")[1])

def parse_cli():
	parser = ArgumentParser(
		description="Take screenshots and short clips from a video",
		formatter_class=ArgumentDefaultsHelpFormatter
	)
	parser.add_argument("--columns",     "-c", type=int,   default=  5,    metavar="COLS", help="Number of columns in montage")
	parser.add_argument("--rows",        "-r", type=int,   default= 10,    metavar="ROWS", help="Number of rows in montage")
	parser.add_argument("--width",       "-w", type=int,   default=320,    metavar="PX",   help="Width of a single screenshot in montage")
	parser.add_argument("--frames",      "-f", type=int,   default= 20,    metavar="N",    help="Number of full size frames")
	parser.add_argument("--clips",       "-C", type=int,   default=  5,    metavar="N",    help="Number of clips")
	parser.add_argument("--clip-width",  "-W", type=int,   default=250,    metavar="PX",   help="Width of clips")
	parser.add_argument("--clip-length", "-L", type=float, default=  3,    metavar="SEC",  help="Length of each clip")
	parser.add_argument("--clip-size",   "-S", type=str,   default= "5MB", metavar="SIZE", help="Maximum total size of all clips")
	parser.add_argument("--cut-start",   "-x", type=float, default=  0,    metavar="SEC",  help="Skip the first part of the source video")
	parser.add_argument("--cut-end",     "-y", type=float, default=  0,    metavar="SEC",  help="Skip the last part of the source video")
	parser.add_argument("--keep",        "-k", action="store_true",                        help="Keep and reuse temporary files")
	parser.add_argument("--prefix",      "-p", type=str,   default="",     metavar="NAME", help="File name prefix")
	parser.add_argument("files", nargs="+")
	args = parser.parse_args()
	args.clip_size = parse_size(args.clip_size)
	return args

def parse_size(s):
	units = {
		"KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40, "PiB": 2**50, "EiB": 2**60, "ZiB": 2**70, "YiB": 2**80,
		"kB": 10** 3, "MB": 10** 6, "GB": 10** 9, "TB": 10**12, "PB": 10**15, "EB": 10**18, "ZB": 10**21, "YB": 10**24,
	}
	for k, v in units.items():
		if s.endswith(k): return float(s[:-len(k)]) * v
	return float(s[:-1]) if s.endswith("B") else float(s)

def main(args):
	returncode = 0
	prefix = args.prefix
	fmt = "{}{:0" + str(digits(len(args.files))) + "}-"
	i = 0
	for filename in args.files:
		try:
			i += 1
			if len(args.files) > 1:
				args.prefix = fmt.format(prefix, i)
			process_video(VideoMetadata(filename), args)
		except CalledProcessError as ex:
			returncode += 1
			sys.stderr.write(ex.stderr.decode(sys.getdefaultencoding()))
			sys.stdout.write(ex.stdout.decode(sys.getdefaultencoding()))
	return returncode

def process_video(video, args):
	dt_frame = video.frame_rate_den / video.frame_rate_num
	length_with_cuts = video.length - args.cut_start - args.cut_end
	if args.clips > 0:
		clip_files = []
		ffinputs = []
		ffoutputs = []
		with open(args.prefix + PLAYLIST_FILE, "w") as playlist:
			for i in range(args.clips):
				clip_file = f"{args.prefix}clip{i + 1:0{digits(args.clips)}d}.mkv"
				clip_files.append(clip_file)
				if not (args.keep and os.path.exists(clip_file)):
					ss = args.cut_start + length_with_cuts * (i + 1) / (args.clips + 1) - args.clip_length / 2
					frames = round(args.clip_length * video.frame_rate_num / video.frame_rate_den)
					ffinputs.append([ "-ss", str(ss), "-i", video.filename ])
					ffoutputs.append([
						"-frames:v", str(frames),
						"-pix_fmt", "yuv444p",
						"-filter:v", f"scale={args.clip_width}:-1,setsar=1",
						"-codec:v", "libx265",
						"-preset:v", "ultrafast",
						"-x265-params", "lossless=1",
						"-an", "-sn", "-dn",
						clip_file
					])
				playlist.write("file '{}'\n".format(clip_file))
		ffmpeg_mimo(ffinputs, ffoutputs)
		depth = MAX_DEPTH
		frame_rate = min(video.frame_rate_num / video.frame_rate_den, MAX_FRAME_RATE)
		multi_palette = True
		s = choose_dither_algo(depth, frame_rate, multi_palette, args.prefix)
		while s > args.clip_size:
			r = args.clip_size / s
			depth, frame_rate = next_guess(depth, frame_rate, multi_palette, r)
			if multi_palette and (depth < MIN_DEPTH or frame_rate < MIN_FRAME_RATE):
				depth = MAX_DEPTH
				frame_rate = min(video.frame_rate_num / video.frame_rate_den, MAX_FRAME_RATE)
				multi_palette = False
			s = choose_dither_algo(depth, frame_rate, multi_palette, args.prefix)
		if not args.keep:
			for f in clip_files:
				os.unlink(f)
			os.unlink(args.prefix + PLAYLIST_FILE)
	if args.frames > 0:
		dt_large = length_with_cuts / (args.frames + 1)
		timestamps = [ args.cut_start + (i + 1) * dt_large for i in range(args.frames) ]
		ffmpeg_mimo(
			[ [ "-ss", str(t), "-to", str(t + dt_frame), "-i", video.filename ] for t in timestamps ],
			[ [ "-frames:v", "1", f"{args.prefix}frame{i + 1:0{digits(args.frames)}d}.png" ] for i in range(args.frames) ]
		)
		for i in range(args.frames):
			filename = f"{args.prefix}frame{i + 1:0{digits(args.frames)}d}.png"
			if os.stat(filename).st_size > args.clip_size:
				os.unlink(filename)
	if args.columns > 0 and args.rows > 0:
		rows = min(max(floor(video.length / (MIN_TIME_DELTA * args.columns)), 1), args.rows)
		cells = args.columns * rows
		dt_cell = video.length / (cells + 1)
		timestamps = [ (i + 1) * dt_cell for i in range(cells) ]
		ffmpeg_mimo(
			[ [ "-ss", str(t), "-to", str(t + dt_frame), "-i", video.filename ] for t in timestamps ],
			[
				[
					"-filter:v", ",".join([
						f"scale={args.width}:-1",
						f"setpts=({i}+1)/TB*{dt_cell}",
						f"drawtext=text=%{{pts\\\:hms}}:fontfile={FONT_FILE}:fontsize={FONT_SIZE}:x=4:y=4:shadowx=-2:shadowy=-2:fontcolor={FONT_COLOR}:shadowcolor={FONT_BACKGROUND}",
					]),
					"-frames:v", "1",
					f"{args.prefix}montage{i:0{digits(cells)}d}.png"
				]
				for i in range(cells)
			]
		)
		montage(f"{args.prefix}montage.png", args.columns, rows, [ f"{args.prefix}montage{i:0{digits(cells)}d}.png" for i in range(cells) ])
		create_label(args.prefix + LABEL_FILE, video.text, args.columns * args.width)
		append_label(f"{args.prefix}montage.jpg", args.prefix + LABEL_FILE, f"{args.prefix}montage.png")
		for i in range(cells):
			os.unlink(f"{args.prefix}montage{i:0{digits(cells)}d}.png")
		os.unlink(f"{args.prefix}montage.png")
		os.unlink(args.prefix + LABEL_FILE)

def digits(n):
	return ceil(log(n + 1, 10))

def choose_dither_algo(depth, frame_rate, multi_palette, prefix):
	dither_algos = {
		prefix + "bayer1.gif":			"dither=bayer:bayer_scale=1",
		prefix + "bayer2.gif":			"dither=bayer:bayer_scale=2",
		prefix + "bayer3.gif":			"dither=bayer:bayer_scale=3",
		prefix + "bayer4.gif":			"dither=bayer:bayer_scale=4",
		prefix + "floyd_steinberg.gif":	"dither=floyd_steinberg",
		prefix + "sierra2.gif":			"dither=sierra2",
		prefix + "sierra2_4a.gif":		"dither=sierra2_4a"
	}
	cmd = [ "-f", "concat", "-safe", "0", "-i", prefix + PLAYLIST_FILE ]
	for name, algo in dither_algos.items():
		cmd += [ "-filter:v", filter_v(depth, frame_rate, algo, multi_palette), name ]
	ffmpeg(*cmd)
	best = None
	for name, algo in dither_algos.items():
		current = ( name, algo, os.stat(name).st_size )
		if not best:
			best = current
		elif current[2] <= best[2]:
			os.unlink(best[0])
			best = current
		else:
			os.unlink(current[0])
	os.rename(best[0], prefix + CLIPS_FILE)
	return best[2]

def filter_v(depth, frame_rate, dither_algo, multi_palette):
	fmt = "fps={},split[a][b],[a]fifo[c],[b]palettegen=max_colors={}:{}:{}[p],[c][p]paletteuse={}:{}"
	stats_mode = "stats_mode=single" if multi_palette else "stats_mode=diff"
	transparent = "reserve_transparent=0" if multi_palette else "reserve_transparent=1"
	new = "new=1" if multi_palette else "new=0"
	return fmt.format(frame_rate, depth, stats_mode, transparent, dither_algo, new)

def next_guess(depth, frame_rate, multi_palette, r):
	if multi_palette:
		new_depth = depth // 2
		new_frame_rate = log(depth, 2) * frame_rate * r / log(new_depth, 2)
		while new_frame_rate > frame_rate:
			new_depth = (new_depth + depth) // 2
			new_frame_rate = log(depth, 2) * frame_rate * r / log(new_depth, 2)
		return (new_depth, new_frame_rate)
	else:
		# TODO: This always underestimates the size, leading to several passes in single palette mode. It's because the
		# lower the framerate, the less of each frame is transparent. Hard to model, take the error into account??
		return (depth, frame_rate * r)

def ffmpeg_mimo(inputs, outputs, limit=10):
	assert len(inputs) == len(outputs)
	# Process `limit` inputs at a time to limit memory usage
	N = len(inputs) // limit
	M = len(inputs) % limit
	for i in range(N):
		a = i * limit
		b = a + limit
		ffmpeg(
			"-vsync", "passthrough",
			*flatten(inputs[a:b]),
			*flatten([ [ "-map", f"{j}:v" ] + out for j, out in zip(range(limit), outputs[a:b]) ])
		)
	if M != 0:
		a = N * limit
		b = a + M
		ffmpeg(
			"-vsync", "passthrough",
			*flatten(inputs[a:b]),
			*flatten([ [ "-map", f"{j}:v" ] + out for j, out in zip(range(M), outputs[a:b]) ])
		)

def flatten(list_of_lists):
	return [val for sublist in list_of_lists for val in sublist]

def ffmpeg(*args):
	print("ffmpeg", " ".join([ shlex.quote(arg) for arg in args ]))
	proc = run(( "ffmpeg", "-y", "-hide_banner" ) + args, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
	proc.check_returncode()

def ffprobe_json(filename):
	proc = run(
		[ "ffprobe", "-hide_banner", "-print_format", "json", "-show_format", "-show_streams", filename ],
		stdin=DEVNULL, stdout=PIPE, stderr=PIPE
	)
	proc.check_returncode()
	return json.loads(proc.stdout.decode(sys.getdefaultencoding()))

def ffprobe_text(filename):
	proc = run(
		[ "ffprobe", "-hide_banner", filename ],
		stdin=DEVNULL, stdout=PIPE, stderr=PIPE
	)
	proc.check_returncode()
	return proc.stderr.decode(sys.getdefaultencoding()).strip()

def montage(outfile, columns, rows, infiles):
	proc = run([ "montage", "-geometry", "+0+0", "-tile", f"{columns}x{rows}" ] + infiles + [ outfile ], stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
	proc.check_returncode()

def create_label(filename, text, width):
	proc = run([
		"convert",
		"-background", FONT_BACKGROUND,
		"-fill", FONT_COLOR,
		"-font", FONT_FILE,
		"-pointsize", str(FONT_SIZE),
		"-size", str(width) + "x",
		"caption:" + text,
		filename
	], stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
	proc.check_returncode()

def append_label(outfile, *args):
	proc = run([
		"convert",
		"-quality", "95",
		"-background", "black",
		"-append"
	] + list(args) + [ outfile ], stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
	proc.check_returncode()

if __name__ == "__main__":
	sys.exit(main(parse_cli()))
