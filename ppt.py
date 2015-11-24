import argparse
import multiprocessing
import json
import subprocess as sp
import yaml

FFMPEG_BIN = 'ffmpeg'
FFPROBE_BIN = 'ffprobe'
CFILE = open('probe/config.yaml', 'r')
CONFIG = yaml.load_all(CFILE)


class PPT:

    """
    Probe a video file for validity and metadata. Use this gathered metadata
    to design an FFmpeg transcode command that is optimized for high quality
    output of short durations.
    USAGE: python probevideo.py -f /path/to/file.ext
    """

    def __init__(self):
        for data in CONFIG:
            self.opts = data
        self.cpus = int(multiprocessing.cpu_count()) - 1

    def analyze(self, filename):
        self.filename = filename
        try:
            open(self.filename)
        except IOError:
            print("The given file failed to be opened!!!")
            print("Filename:" + self.filename)
            return
        # If the file can be opened we may begin analyzing the container
        analyze = [FFPROBE_BIN, '-v', 'quiet', '-show_format', '-show_streams',
                   '-print_format', 'json', self.filename]
        probe_data = sp.check_output(analyze, bufsize=10**8, stderr=sp.STDOUT)
        self.probe_res = json.loads(
            probe_data.decode(encoding='UTF-8'), 'latin-1')
        self.analysis = json.dumps(self.probe_res, indent=4)
        stm_data = self.probe_res["streams"]
        for stream in stm_data:
            if stream["codec_type"] in 'video':
                self.video_stream = stream
                print("Found video stream in the file.")
        print(self.video_stream)
        mbps = int(self.video_stream["bit_rate"]) / 1000000
        # Determine new bitrate when source bit rate is too high to stream
        if mbps > 5:
            if mbps / 2 > 11:
                if mbps / 2 > 11 and mbps / 2 < 20:
                    new_mbps = mbps / 2
                else:
                    new_mbps = 14
            else:
                new_mbps = mbps / 2.25
        else:
            new_mbps = mbps
        if new_mbps > 8:
            new_mbps = round(new_mbps)
        else:
            new_mbps = round(new_mbps)
            # Determine minimum+maximum bit rate + buffer size
            # When VP10 is released, these vals will matter!
            min_rate = round(new_mbps * 0.85)
            max_rate = round(new_mbps * 1.5)
            buf_size = round(max_rate * 1.75)
        self.target = new_mbps * 1000
        print("New target bitrate: " + str(self.target))

    def build_y4m(self):
        print("Converting source video stream to Y4M rawvideo container.")
        y4m_start = [FFMPEG_BIN, '-y', '-i', self.filename]
        y4m_codec = self.opts['formats']['y4m']['codec'].split(' ')
        y4m_opts = self.opts['formats']['y4m']['options'].split(' ')
        self.y4m_out = self.filename + '.y4m'
        y4m_cmd = y4m_start + y4m_codec + y4m_opts + [self.y4m_out]
        print(y4m_cmd)
        sp.check_output(y4m_cmd, stderr=sp.STDOUT)
        print("Y4M creation complete.")

    def build_audio(self):
        print("Stream copying source audio to WAV for OGG conversion.")
        self.wav_in = self.filename + '.wav'
        wav_cmd = [FFMPEG_BIN, '-y', '-i', self.filename, '-vn', '-sn', '-map',
                   '0:a', self.wav_in]
        sp.check_output(wav_cmd, stderr=sp.STDOUT)
        print("Converting source audio into OGG Vorbis for WebM stream copy.")
        self.ogg_out = self.filename + '.ogg'
        ogg_start = ['oggenc', self.wav_in, '-o', self.ogg_out]
        sp.check_output(ogg_start, stderr=sp.STDOUT)
        print("OGG Vorbis conversion complete.")
        print("Encoding WAV audio into OPUS for WebM stream copy.")
        self.opus_out = self.filename + '.opus'
        opus_cmd = ['opusenc', '--bitrate', '160', self.wav_in, self.opus_out]
        sp.check_output(opus_cmd, stderr=sp.STDOUT)
        print("OPUS encoding complete.")

    def build_vp8(self):
        print("Converting source video to VP8 stream.")
        y4m_in = self.filename + '.y4m'
        vp8_opts = self.opts['formats']['vp8']['vpxenc'].split(' ')
        self.vp8_out = self.filename + '.vp8.webm'
        vp8_bits = '--target-bitrate=' + str(self.target)
        vp8_args = [vp8_bits, '-o', self.vp8_out, y4m_in]
        vp8_cmd = vp8_opts + vp8_args
        print(vp8_cmd)
        sp.check_output(vp8_cmd, stderr=sp.STDOUT)
        print("VP8 conversion complete.")

    def build_vp9(self):
        print("Converting source video to VP9 stream.")
        y4m_in = self.filename + '.y4m'
        self.vp9_out = self.filename + '.vp9.webm'
        vp9_target = int(self.target / 1.5)
        vp9_opts = self.opts['formats']['vp9']['vpxenc'].split(' ')
        vp9_bits = '--target-bitrate=' + str(vp9_target)
        vp9_args = [vp9_bits, '-o', self.vp9_out, y4m_in]
        vp9_cmd = vp9_opts + vp9_args
        print(vp9_cmd)
        sp.check_output(vp9_cmd, stderr=sp.STDOUT)
        print("VP9 conversion complete.")

    def build_webm(self):
        self.webm_ref = self.filename + '.ref.webm'
        self.vp8_ref = '.vp8' + self.webm_ref
        self.vp9_ref = '.vp9' + self.webm_ref
        # Put it all together
        ffmpeg_copy_vp8 = [FFMPEG_BIN, '-y', '-i', self.vp8_out, '-i',
                           self.ogg_out, '-c', 'copy', '-flags',
                           '+global_header', self.vp8_ref]
        sp.check_output(ffmpeg_copy_vp8, stderr=sp.STDOUT)
        ffmpeg_copy_vp9 = [FFMPEG_BIN, '-y', '-i', self.vp9_out, '-i',
                           self.opus_out, '-c', 'copy', '-flags',
                           '+global_header', self.vp9_ref]
        sp.check_output(ffmpeg_copy_vp9, stderr=sp.STDOUT)

    def multi_webm(self):
        # Now that the primary encodes are done, we can make the compatability
        # encodes. These are to be created from the initial 2-pass encode.
        # Note: this method outputs a string to a shell script for execution.
        # This is done in order to allow the server admin to designate niceness
        # on a per-job basis. Regardless, Python's subprocess module cannot
        # reconcile the canonical FFmpeg syntax required to run this command.
        if self.video_stream["coded_height"] >= 1080:
            mult_begin = 'webm_1080'
        elif self.video_stream["coded_height"] >= 720:
            mult_begin = 'webm_720'
        elif self.video_stream["coded_height"] >= 480:
            mult_begin = 'webm_480'
        elif self.video_stream["coded_height"] >= 720:
            mult_begin = 'webm_360'
        else:
            mult_begin = 'webm_280'
        # Determine which FPS scale the video outputs get
        fps = self.video_stream["r_frame_rate"]
        fps_scale = eval(fps)
        fps_mult = [30, 60]
        if min(fps_mult, key=lambda x: abs(x - fps_scale)) == 60:
            scale = 'scales_60'  # 60 fps bitrate
        else:
            scale = 'scales_30'  # 30 fps bitrate
        # Create the complex filter graph FFmpeg command
        mult_complex = [self.opts[scale][mult_begin]['complex']]
        mult_mapping = self.opts[scale][mult_begin]['mapping']
        mult_map = []
        map_num = 1
        for out in mult_mapping:
            mult_map.append('-map')
            mult_map.append('"[' + out + ']"')
            map_cmd = mult_mapping[out].split(' ')
            for fc in map_cmd:
                mult_map.append(fc)
            map_num += 1
        mult_start = [FFMPEG_BIN, '-y', '-i', self.webm_ref]
        mult_command = ['-filter_complex'] + mult_complex + mult_map
        mult_sh = mult_start + mult_command
        print("Multiple-bitrate compatability encodes:")
        print(mult_sh)
        cc_output = ""
        for c in mult_sh:
            cc_output += c
            cc_output += " "
        f = open("ffmpeg.sh", "w")
        f.write(cc_output)
        f.close()
        sp.check_output(['sh', 'ffmpeg.sh'], stderr=sp.STDOUT)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--file')
    args = parser.parse_args()
    print(args.file)
    p = PPT()
    p.analyze(args.file)  # Grab the file, probe for validity, analyze metadata
    p.build_y4m()  # Create the source file for use on x264 and VP8 ref videos
    p.build_audio()  # Grab the file's audio stream, output OGG+OPUS
    p.build_vp8()  # Create the VP8 stream for insertion into WebM container
    p.build_vp9()  # Create the VP9 stream for insertion into WebM container
    p.build_webm()  # Build the reference WebM file, from which all others stem
    p.multi_webm()  # Create multi-output FFmpeg script for all other WebMs
