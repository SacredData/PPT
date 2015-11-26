# ProbeParseTranscode

PPT does precisely what the name suggests. First, it probes the incoming video file for relevant data. Then, it parses that data and makes some decisions. Then, it delivers a crap-ton of transcodes as to consider most playback situations, usecases, and limitations.

My goal in developing PPT is to create a single script that can make all the WebM/libvpx container files a video publisher, CDN, hobbyist, etc., could ever need or want.

## Notes

* This code has system requirements. If you don't have a modern processor with a minimum of four cores, you can't play with us.
* At the moment this code is safe but it is certainly not optimized. Don't pull this repo yet, unless you're 100% certain to not be upset if anything goes wrong.

## Deps/Setup/Usage

First things first: you'll need the most up-to-date FFmpeg possible. If you cannot build the nightlies, at least get the latest version offered by your OS's package management system. Please note that you will also require a more-or-less complete configuration of FFmpeg. Make sure that when you configured FFmpeg, you compiled support for `libvpx`, `libopus`, and `libvorbis`.

Next, hit up pip and place an order for the PyYAML package if you don't already have it.

`sudo pip install pyyaml`

After cloning this repo, head into the root directory and open up ppt.py. Locate the following code blocks near the top:

```
FFMPEG_BIN = 'ffmpeg'
FFPROBE_BIN = 'ffprobe'
CFILE = open('probe/config.yaml', 'r')
CONFIG = yaml.load_all(CFILE)
output = mp.Queue()
```

Make sure that the top two variables, FFMPEG_BIN and FFPROBE_BIN, are right for your system.

If all is well and good up to this point, then you should just be able to:

`python ppt.py -f /path/to/file.ext`

Be ready to watch the pot of water boil for a long time. (But not as long as most other programs would take!)