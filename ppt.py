from multiprocessing import Lock, Pool
import argparse
import json
import fcntl
import subprocess as sp
import sys
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
    The script operates under the following synchronous workflow:
    INPUT_VIDEO -> [OGG, OPUS]  +  [Y4M] -----> [VPx] -------> [WebM]
                      audio       rawvideo     vp8/vp9         vp8/vp9
                        |            |            |               |
                        |            |            |               |
                        v            v            v               v
    METHOD: init()  build_audio() build_y4m() build_vpx()   multi_webm()
    """

    def __init__(self, filename):
        """
        All a user should need to do is supply the initialize method with
        a valid video stream file. The class will do the rest.
        """
        for data in CONFIG:
            self.opts = data
        self.cpus = int(mp.cpu_count())
        if self.cpus < 4:
            sys.exit("Ahh, hell nah! You need 4 cores to play, son!")
        lock = Lock()
        # Lock the file so that other processes don't mess with our shizz.
        try:
            x = open(filename)
            fcntl.flock(x, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.filename = filename
        except IOError:
            er_desc = filename + " cannot be opened. Cannot continue."
            sys.exit(str(er_desc))
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
        if all((src[0], src[1], src[2])):
            bitrate = src[0]
            height = src[1]
            print("Source build successful! \
                Proceeding with target bitrate of", str(bitrate))
        else:
            sys.exit("Source build data missing! Cannot continue.")
        # bitrate = int(bitrate * 2)
        return [int(bitrate * 2), height]

    def build_vids(self, l, br):
        """
        Pool initializer and controller method. Requires a Lock() object to
        unlock the method. If unlocked, will send multiple vpxenc processes
        into an asynchronous processing pool. Once all operations have
        completed, the lock will be re-engaged.
        """
        l.acquire()
        y4m = self.filename + '.y4m'
        vpx = ['vp8']
        with Pool(processes=2) as pool:
            results = [pool.apply_async(self.build_vpx,
                                        args=(y4m, br, x)) for x in vpx]
            procpool = [par.get() for par in results]
        for p in procpool:
            print("Results for ", p[0], ":  ")
            print("Video Stream Data:  ", p[1])
            print("Success:  ", p[3])
        l.release()

    def analyze(self):
        """
        Run analysis on the input file, ensuring that the media file
        contains a valid video stream. The data collected from this
        analysis is to inform all other methods within this class.
        """
        # If the file can be opened we may begin analyzing the container
        analyze = [FFPROBE_BIN, '-v', 'quiet', '-show_format', '-show_streams',
                   '-print_format', 'json', self.filename]
        probe_data = sp.check_output(analyze, bufsize=10**8, stderr=sp.STDOUT)
        probe_res = json.loads(
            probe_data.decode(encoding='UTF-8'), 'latin-1')
        stm_data = probe_res["streams"]
        for stream in stm_data:
            if stream["codec_type"] in 'video':
                self.video_stream = stream
                print("Found video stream in the file.")
            else:
                sys.exit("Error: No video stream found! Cannot continue.")
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
        target = new_mbps * 1000 * 2
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
        y4m_start = [FFMPEG_BIN, '-y', '-i', self.filename]
        y4m_codec = self.opts['formats']['y4m']['codec'].split(' ')
        y4m_opts = self.opts['formats']['y4m']['options'].split(' ')
        y4m_out = self.filename + '.y4m'
        y4m_cmd = y4m_start + y4m_codec + y4m_opts + [y4m_out]
        print("Converting source video stream to Y4M rawvideo container.")
        try:
            sp.check_output(y4m_cmd, stderr=sp.STDOUT)
            print("Y4M creation complete.")
            output.put(True)
        except sp.SubprocessError:
            sys.exit("Error: Y4M rawvideo generation failed. Cannot continue.")

    def build_audio(self):
        """
        Extract the video container's audio stream and encode the data
        into both OGG Vorbis (vp8) and OPUS (vp9).
        """
        print("Stream copying source audio to WAV for OGG conversion.")
        try:
            wav_in = self.filename + '.wav'
            wav_cmd = [FFMPEG_BIN, '-y', '-i', self.filename, '-vn', '-sn',
                       '-map', '0:a', wav_in]
            sp.check_output(wav_cmd, stderr=sp.STDOUT)
        except sp.SubprocessError:
            print("Error: WAV audio extraction failed. Where da audio at?")
            return
        else:
            audio_res = 0
            print("Generating OGG Vorbis from WAV audio.")
            try:
                ogg_out = self.filename + '.ogg'
                ogg_start = ['oggenc', wav_in, '-o', ogg_out]
                sp.check_output(ogg_start, stderr=sp.STDOUT)
                print("OGG Vorbis conversion complete.")
            except sp.SubprocessError:
                print("Error: OGG Vorbis conversion failed! However, we will \
                    continue to attempt generating the other audio formats.")
                audio_res += 1
            print("Generating OPUS from WAV audio.")
            try:
                self.opus_out = self.filename + '.opus'
                opus_cmd = ['opusenc', '--bitrate', '160', wav_in,
                            self.opus_out]
                sp.check_output(opus_cmd, stderr=sp.STDOUT)
                print("OPUS encoding complete.")
            except sp.SubprocessError:
                print("Error: OPUS conversion failed!")
                audio_res += 1
            if audio_res > 0:
                output.put(False)  # WebM shouldn't attempt audio
            else:
                output.put(True)   # WebM should attempt audio

    def build_vpx(self, y4m, target, vp='vp8'):
        """
        This method will convert a Y4M (4:2:0 colorspace) raw video stream
        into a reference-level VP8/VP9 video stream contained within a
        WebM file. The target argument must be an array: [target_bit_rate,
        multi_webm_start]. Provide this data as [int, str], respectively.
        """
        print("Converting source video to stream:  ", vp)
        vp_opts = self.opts['formats'][vp]['vpxenc'].split(' ')
        vp_out = self.filename + '.' + str(vp) + '.webm'
        vp_bits = '--target-bitrate=' + str(target[0])
        vp_args = [vp_bits, '-o', vp_out, y4m]
        vp_cmd = vp_opts + vp_args
        try:
            sp.check_output(vp_cmd, stderr=sp.STDOUT)
            print(vp, " conversion complete:  ", vp_out)
        except sp.SubprocessError:
            print("Error: ", vp, " conversion.")
            return
        self.multi_webm(vp_out, target[1])
        return [vp, vp_out, True]

    def multi_webm(self, ref, mult_begin, fps=60):
        """
        Create the multi_webm FFmpeg shell script and execute it.
        NOTE: this method outputs a string to a shell script for execution.
        For this reason, IT MAY NOT BE SAFE TO RUN IN EVERY USE-CASE.
        This is done in order to allow the server admin to designate niceness
        on a per-job basis.
        """
        try:
            y = open(ref)
            fcntl.flock(y, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            print("Filename: " + ref + " failed to be opened!")
            return
        # Now that the primary encodes are done, we can make the compatability
        # encodes. These are to be created from the initial 2-pass encode.
        print("Beginning with the ", mult_begin, " profile.")
        fps = str(60)
        fps_scale = eval(fps)
        fps_mult = [30, 60]
        if min(fps_mult, key=lambda x: abs(x - fps_scale)) == 60:
            scale = 'scales_60'  # 60 fps bitrate. Progressive scanning!
        else:
            scale = 'scales_30'  # 30 fps bitrate. Consider using yadif?
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
        print("Multiple-bitrate compatability encodes:  " + mult_sh)
        # Get the FFmpeg arguments ready for output to shell script
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
