#!/usr/bin/python3 -O
# vim: noexpandtab, tabstop=4, number
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter, Namespace
from configparser import ConfigParser
from glob import glob
import json
from math import ceil, floor, log, sqrt
import os
import shlex
from subprocess import run, DEVNULL, PIPE, CalledProcessError
import sys

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

	@property
	def dt_frame(self): return self.frame_rate_den / self.frame_rate_num

PLAYLIST_FILE = "playlist.txt"
CLIPS_FILE = "clips.gif"
LABEL_FILE = "label.png"
DEFAULT_ARGS = Namespace(
	frames = 20,
	file_size_max = "5MB",
	font_file = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
	font_size = 16,
	font_color = "white",
	font_background = "black",
	gif_color_depth_min = 96,
	gif_color_depth_max = 256,
	gif_frame_rate_min = 12,
	gif_frame_rate_max = 50,
	gif_clips = 5,
	gif_length = 3,
	gif_width = 250,
	webp_clips = 0,
	webp_length = 5,
	webp_width = 250,
	montage_columns = 5,
	montage_rows = 10,
	montage_cell_width = 320,
	montage_time_delta_min = 10
)

def parse_config(filename):
	configfile = ConfigParser()
	configfile.read(filename)
	args = Namespace()
	config_option(configfile, "main", "frames", args, "frames", int)
	config_option(configfile, "main", "file.size.max", args, "file_size_max")
	config_option(configfile, "gif", "color.depth.min", args, "gif_color_depth_min", int)
	config_option(configfile, "gif", "color.depth.max", args, "gif_color_depth_max", int)
	config_option(configfile, "gif", "frame.rate.min", args, "gif_frame_rate_min", float)
	config_option(configfile, "gif", "frame.rate.max", args, "gif_frame_rate_max", float)
	config_option(configfile, "gif", "clips", args, "gif_clips", int)
	config_option(configfile, "gif", "length", args, "gif_length", float)
	config_option(configfile, "gif", "width", args, "gif_width", int)
	config_option(configfile, "webp", "clips", args, "webp_clips", int)
	config_option(configfile, "webp", "length", args, "webp_length", float)
	config_option(configfile, "webp", "width", args, "webp_width", int)
	config_option(configfile, "montage", "columns", args, "montage_columns", int)
	config_option(configfile, "montage", "rows", args, "montage_rows", int)
	config_option(configfile, "montage", "cell.width", args, "montage_cell_width", int)
	config_option(configfile, "montage", "time.delta.min", args, "montage_time_delta_min", float)
	config_option(configfile, "font", "file", args, "font_file")
	config_option(configfile, "font", "size", args, "font_size", int)
	config_option(configfile, "font", "color", args, "font_color")
	config_option(configfile, "font", "background", args, "font_background")
	return args

def config_option(configfile, section, option, namespace, name, f=str):
	default_value = getattr(DEFAULT_ARGS, name)
	setattr(namespace, name, f(configfile.get(section, option, fallback=default_value)))

def parse_cli():
	config = parse_config(os.path.join(os.environ["HOME"], ".torrentutils", "screenshots.conf"))
	parser = ArgumentParser(
		description="Take screenshots and short clips from a video",
		formatter_class=ArgumentDefaultsHelpFormatter
	)

	parser.add_argument("files", nargs="+")

	group = parser.add_argument_group("common arguments")
	group.add_argument("--prefix", "-p", required=True,
		metavar="NAME", help="File name prefix")
	group.add_argument("--cut-start", "-x", type=float, default=0,
		metavar="SEC", help="Skip the first part of the source video")
	group.add_argument("--cut-end", "-y", type=float, default=0,
		metavar="SEC",  help="Skip the last part of the source video")
	group.add_argument("--keep", "-k", action="store_true",
		help="Keep and reuse temporary files")
	group.add_argument("--frames","-f", type=int, default=config.frames,
		metavar="N", help="Number of full size frames")
	group.add_argument("--file-size-max", type=str, default=config.file_size_max,
		metavar="SIZE", help="Maximum total size of all clips")

	group = parser.add_argument_group("GIF arguments")
	group.add_argument("--gif-clips", type=int, default=config.gif_clips,
		metavar="N", help="Number of clips")
	group.add_argument("--gif-length", type=float, default=config.gif_length,
		metavar="SEC",  help="Length of each clip")
	group.add_argument("--gif-width", type=int, default=config.gif_width,
		metavar="PX",   help="Width of clips")
	group.add_argument("--gif-color-depth-min", type=int, default=config.gif_color_depth_min,
		metavar="N", help="Minimum color depth to try before switching to single palette mode")
	group.add_argument("--gif-color-depth-max", type=int, default=config.gif_color_depth_max,
		metavar="N", help="Maximum color depth to try")
	group.add_argument("--gif-frame-rate-min", type=float, default=config.gif_frame_rate_min,
		metavar="FPS", help="Minimum frame rate to try before switching to single palette mode")
	group.add_argument("--gif-frame-rate-max", type=float, default=config.gif_frame_rate_max,
		metavar="FPS", help="Maximum frame rate to try")

	group = parser.add_argument_group("WEBP arguments")
	group.add_argument("--webp-clips", type=int, default=config.webp_clips,
		metavar="N", help="Number of clips")
	group.add_argument("--webp-length", type=float, default=config.webp_length,
		metavar="SEC",  help="Length of each clip")
	group.add_argument("--webp-width", type=int, default=config.webp_width,
		metavar="PX",   help="Width of clips")

	group = parser.add_argument_group("montage arguments")
	group.add_argument("--montage-columns", type=int, default=config.montage_columns,
		metavar="COLS", help="Number of columns in montage")
	group.add_argument("--montage-rows", type=int, default=config.montage_rows,
		metavar="ROWS", help="Number of rows in montage")
	group.add_argument("--montage-cell-width", type=int, default=config.montage_cell_width,
		metavar="PX",   help="Width of a single screenshot in montage")
	group.add_argument("--montage-time-delta-min", type=float, default=config.montage_time_delta_min,
		metavar="SEC", help="Minimum time between cells")

	group = parser.add_argument_group("font arguments")
	group.add_argument("--font-file", default=config.font_file,
		metavar="FILE", help="TTF font file")
	group.add_argument("--font-size", type=int, default=config.font_size,
		metavar="SIZE", help="Font size")
	group.add_argument("--font-color", default=config.font_color,
		metavar="COLOR", help="Font color")
	group.add_argument("--font-background", default=config.font_background,
		metavar="COLOR", help="Background color")
	
	args = parser.parse_args()
	args.file_size_max = parse_size(args.file_size_max)
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
	if args.gif_clips > 0:
		clip_files = prepare_clips(video, args, args.gif_clips, args.gif_length, args.gif_width)
		create_gif(video, args)
		cleanup_clips(args, clip_files)
	if args.webp_clips > 0:
		clip_files = prepare_clips(video, args, args.webp_clips, args.webp_length, args.webp_width)
		create_webp(video, args)
		cleanup_clips(args, clip_files)
	if args.frames > 0:
		create_frames(video, args)
	if args.montage_columns > 0 and args.montage_rows > 0:
		create_montage(video, args)

def digits(n):
	return ceil(log(n + 1, 10))

def prepare_clips(video, args, clips, length, width):
	length_with_cuts = video.length - args.cut_start - args.cut_end
	clip_files = []
	ffinputs = []
	ffoutputs = []
	with open(args.prefix + PLAYLIST_FILE, "w") as playlist:
		for i in range(clips):
			clip_file = f"{args.prefix}clip{i + 1:0{digits(clips)}d}.mkv"
			clip_files.append(clip_file)
			if not (args.keep and os.path.exists(clip_file)):
				ss = args.cut_start + length_with_cuts * (i + 1) / (clips + 1) - length / 2
				frames = round(length * video.frame_rate_num / video.frame_rate_den)
				ffinputs.append([ "-ss", str(ss), "-i", video.filename ])
				ffoutputs.append([
					"-frames:v", str(frames),
					"-pix_fmt", "yuv444p",
					"-filter:v", f"scale={width}:-1,setsar=1",
					"-codec:v", "libx265",
					"-preset:v", "ultrafast",
					"-x265-params", "lossless=1",
					"-an", "-sn", "-dn",
					clip_file
				])
			playlist.write("file '{}'\n".format(clip_file))
	ffmpeg_mimo(ffinputs, ffoutputs)
	return clip_files

def create_gif(video, args):
	depth = args.gif_color_depth_max
	frame_rate = min(video.frame_rate_num / video.frame_rate_den, args.gif_frame_rate_max)
	multi_palette = True
	s = choose_dither_algo(depth, frame_rate, multi_palette, args.prefix)
	while s > args.file_size_max:
		r = args.file_size_max / s
		depth, frame_rate = next_guess(depth, frame_rate, multi_palette, r)
		if multi_palette and (depth < args.gif_color_depth_min or frame_rate < args.gif_frame_rate_min):
			depth = args.gif_color_depth_max
			frame_rate = min(video.frame_rate_num / video.frame_rate_den, args.gif_frame_rate_max)
			multi_palette = False
		s = choose_dither_algo(depth, frame_rate, multi_palette, args.prefix)

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

def create_webp(video, args):
	filename = args.prefix + "clips.webp"
	size_factor = 1.05 # TODO: learn & store size factor
	size = None
	quality = 95
	while size == None or args.file_size_max < size:
		print(size, "/", args.file_size_max)
		ffmpeg(
			"-f", "concat",
			"-safe", "0",
			"-i", args.prefix + PLAYLIST_FILE,
			"-c:v", "libwebp",
			"-loop", "0",
			"-compression_level", "6",
			"-q:v", str(quality),
			filename
		)
		size = os.stat(filename).st_size
		quality -= ceil(log(size / args.file_size_max, size_factor))

def cleanup_clips(args, clip_files):
	if not args.keep:
		for f in clip_files:
			os.unlink(f)
		os.unlink(args.prefix + PLAYLIST_FILE)

def create_frames(video, args):
	length_with_cuts = video.length - args.cut_start - args.cut_end
	dt_large = length_with_cuts / (args.frames + 1)
	timestamps = [ args.cut_start + (i + 1) * dt_large for i in range(args.frames) ]
	ffmpeg_mimo(
		[ [ "-ss", str(t), "-to", str(t + video.dt_frame), "-i", video.filename ] for t in timestamps ],
		[ [ "-frames:v", "1", f"{args.prefix}frame{i + 1:0{digits(args.frames)}d}.png" ] for i in range(args.frames) ]
	)
	for i in range(args.frames):
		filename = f"{args.prefix}frame{i + 1:0{digits(args.frames)}d}.png"
		if os.stat(filename).st_size > args.file_size_max:
			os.unlink(filename)

def create_montage(video, args):
	rows = min(max(floor(video.length / (args.montage_time_delta_min * args.montage_columns)), 1), args.montage_rows)
	cells = args.montage_columns * rows
	dt_cell = video.length / (cells + 1)
	timestamps = [ (i + 1) * dt_cell for i in range(cells) ]
	ffmpeg_mimo(
		[ [ "-ss", str(t), "-to", str(t + video.dt_frame), "-i", video.filename ] for t in timestamps ],
		[
			[
				"-filter:v", ",".join([
					f"scale={args.montage_cell_width}:-1",
					f"setpts=({i}+1)/TB*{dt_cell}",
					f"drawtext=text=%{{pts\\\:hms}}:fontfile={args.font_file}:fontsize={args.font_size}:x=4:y=4:shadowx=-2:shadowy=-2:fontcolor={args.font_color}:shadowcolor={args.font_background}",
				]),
				"-frames:v", "1",
				f"{args.prefix}montage{i:0{digits(cells)}d}.png"
			]
			for i in range(cells)
		]
	)
	montage(f"{args.prefix}montage.png", args.montage_columns, rows, [ f"{args.prefix}montage{i:0{digits(cells)}d}.png" for i in range(cells) ])
	create_label(video, args)
	append_label(f"{args.prefix}montage.jpg", args.prefix + LABEL_FILE, f"{args.prefix}montage.png")
	for i in range(cells):
		os.unlink(f"{args.prefix}montage{i:0{digits(cells)}d}.png")
	os.unlink(f"{args.prefix}montage.png")
	os.unlink(args.prefix + LABEL_FILE)

def montage(outfile, columns, rows, infiles):
	proc = run([ "montage", "-geometry", "+0+0", "-tile", f"{columns}x{rows}" ] + infiles + [ outfile ], stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
	proc.check_returncode()

def create_label(video, args):
	proc = run([
		"convert",
		"-background", args.font_background,
		"-fill", args.font_color,
		"-font", args.font_file,
		"-pointsize", str(args.font_size),
		"-size", str(args.montage_columns * args.montage_cell_width) + "x",
		"caption:" + video.text,
		args.prefix + LABEL_FILE
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

if __name__ == "__main__":
	sys.exit(main(parse_cli()))
