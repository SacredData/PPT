from multiprocessing import Lock, Pool
import argparse
import json
import fcntl
import subprocess as sp
import yaml
import multiprocessing as mp

FFMPEG_BIN = 'ffmpeg'
FFPROBE_BIN = 'ffprobe'
CFILE = open('probe/config.yaml', 'r')
CONFIG = yaml.load_all(CFILE)
output = mp.Queue()


class PPT:

    """
    Probe a video file for validity and metadata. Use this gathered metadata
    to design an FFmpeg transcode command that is optimized for high quality
    output of short durations.
    USAGE: python ppt.py -f /path/to/file.ext
    """

    def __init__(self, filename):
        for data in CONFIG:
            self.opts = data
        self.cpus = int(mp.cpu_count())
        self.proc_max = round(self.cpus / 1.5)
        if self.proc_max < 4:
            print("Ahh, hell nah! You need 6 cores to play, son!")
            return
        self.filename = filename
        lock = Lock()
        # Lock the file so that other processes don't mess with our shizz.
        try:
            x = open(self.filename)
            fcntl.flock(x, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            print("The given file failed to be opened!!!")
            print("Filename:" + self.filename)
            return
        br = self.prepare()
        if br:
            fcntl.flock(x, fcntl.LOCK_UN)  # unlock input file
            x.close()
            self.build_vids(lock, br)  # start the intense stuff

    def prepare(self):
        bitrate = False
        sources = [
            mp.Process(target=self.analyze), mp.Process(
                target=self.build_audio),
            mp.Process(target=self.build_y4m)]
        for p in sources:
            p.start()
        for p in sources:
            p.join()
        src = [output.get() for p in sources]
        print(src)
        if all((src[0], src[1], src[2])):
            print("Source build successful!")
            bitrate = src[0]
            height = src[1]
            print("Proceeding with target bitrate of", str(bitrate))
        return [bitrate, height]

    def build_vids(self, l, br):
        l.acquire()
        y4m = self.filename + '.y4m'
        vpx = ['vp8', 'vp9']
        # vp8_ref = self.build_vpx(y4m, br, 'vp8')
        # if vp8_ref:
        with Pool(processes=2) as pool:
            results = [
                pool.apply_async(self.build_vpx, args=(y4m, br, x)) for x in vpx]
            procpool = [par.get() for par in results]
        print(procpool)
        l.release()

    def analyze(self):
        """
        Run analysis on the input file, ensuring that the media file
        contains a valid video stream. The data collected from this
        analysis is to inform all other methods within this class.
        Therefore, it is a MUST RUN. :)
        """
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
        target = new_mbps * 1000
        output.put(target)
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
        print("New target bitrate: " + str(target))
        print("Video height: " + str(self.video_stream["coded_height"]))
        output.put(mult_begin)

    def build_y4m(self):
        """
        Create the Y4M file, which will serve as the reference video data
        from which we will make our reference VP8 and VP9 streams.
        """
        print("Converting source video stream to Y4M rawvideo container.")
        y4m_start = [FFMPEG_BIN, '-y', '-i', self.filename]
        y4m_codec = self.opts['formats']['y4m']['codec'].split(' ')
        y4m_opts = self.opts['formats']['y4m']['options'].split(' ')
        y4m_out = self.filename + '.y4m'
        y4m_cmd = y4m_start + y4m_codec + y4m_opts + [y4m_out]
        print(y4m_cmd)
        sp.check_output(y4m_cmd, stderr=sp.STDOUT)
        print("Y4M creation complete.")
        output.put(True)

    def build_audio(self):
        """
        Extract the video container's audio stream and encode the data
        into both OGG Vorbis (vp8) and OPUS (vp9).
        """
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
        output.put(True)

    def build_vpx(self, y4m, target, vp='vp8'):
        print("Converting source video to stream:  ", vp)
        vp_opts = self.opts['formats'][vp]['vpxenc'].split(' ')
        vp_out = self.filename + '.' + str(vp) + '.webm'
        vp_bits = '--target-bitrate=' + str(target[0])
        vp_args = [vp_bits, '-o', vp_out, y4m]
        vp_cmd = vp_opts + vp_args
        try:
            sp.check_output(vp_cmd, stderr=sp.STDOUT)
        finally:
            print(vp, " conversion complete:  ", vp_out)
            ref_webm = self.build_webm(vp_out, vp)
            mult_webm = self.multi_webm(ref_webm, target[1])
            return [vp, vp_out, ref_webm, True]

    def build_webm(self, vp_in, vpx='vp8'):
        vpref = vp_in + '.' + vpx + '.webm'
        # Put it all together
        if vpx in 'vp8':
            audio = self.filename + '.ogg'
            webm_streamcopy = [FFMPEG_BIN, '-y', '-i', vp_in, '-i',
                               audio, '-c', 'copy', '-flags',
                               '+global_header', vpref]
        elif vpx in 'vp9':
            audio = self.filename + '.opus'
            webm_streamcopy = [FFMPEG_BIN, '-y', '-i', vp_in, '-i',
                               audio, '-c', 'copy', '-flags',
                               '+global_header', vpref]
        sp.check_output(webm_streamcopy, stderr=sp.STDOUT)
        return vpref

    def multi_webm(self, ref, mult_begin, fps=30):
        """
        Create the multi_webm FFmpeg shell script and execute it.
        NOTE: this method outputs a string to a shell script for execution.
        For this reason, IT MAY NOT BE SAFE TO RUN IN EVERY USE-CASE.
        This is done in order to allow the server admin to designate niceness
        on a per-job basis. Regardless, Python's subprocess module cannot
        reconcile the canonical FFmpeg syntax required to run this command.
        """
        try:
            y = open(ref)
            fcntl.flock(y, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            print("The given file failed to be opened!!!")
            print("Filename: " + ref)
            return
        # Now that the primary encodes are done, we can make the compatability
        # encodes. These are to be created from the initial 2-pass encode.
        print("Beginning with the ", mult_begin, " profile.")
        # Determine which FPS scale the video outputs get
        fps = str(30)  # self.video_stream["r_frame_rate"]
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
        mult_start = [FFMPEG_BIN, '-y', '-i', ref]
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
        fcntl.flock(y, fcntl.LOCK_UN)  # unlock input file
        y.close()
        return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--file')
    args = parser.parse_args()
    print(args.file)
    p = PPT(args.file)
