import os
from pathlib import Path
import subprocess
from argparse import Namespace

from caelestia.utils.paths import c_cache_dir


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        if self.args.show:
            # Print the ipc
            self.print_ipc()
        elif self.args.log:
            # Print the log
            self.print_log()
        elif self.args.kill:
            # Kill the shell
            self.shell("kill")
        elif self.args.message:
            # Send a message
            self.message(*self.args.message)
        else:
            # Hardware Decoder Injection
            # Both vars must always be set: Qt's FFmpeg backend probes CUDA/VDPAU/VAAPI
            # devices at plugin init whenever either list is unset, which pins an
            # NVIDIA GPU awake (no D3 sleep). "," is Qt's documented empty list.
            try:
                decoder = "none"
                decoder_file = Path(os.path.expanduser("~/.cache/caelestia/hwDecoder.txt"))
                if decoder_file.exists():
                    decoder = decoder_file.read_text().strip() or "none"

                if decoder.lower() == "auto":
                    # Qt's preferred Linux order (CUDA, VAAPI); parsed lazily, so no
                    # devices are created until a video is actually decoded.
                    os.environ["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = "cuda,vaapi"
                    os.environ["QT_FFMPEG_ENCODING_HW_DEVICE_TYPES"] = "cuda,vaapi"
                elif decoder.lower() == "none":
                    os.environ["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = ","
                    os.environ["QT_FFMPEG_ENCODING_HW_DEVICE_TYPES"] = ","
                else:
                    os.environ["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = decoder
                    os.environ["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = decoder

            except Exception:
                pass

            # Start the shell
            args = ["qs", "-c", "caelestia", "-n"]
            if self.args.log_rules:
                args.extend(["--log-rules", self.args.log_rules])
            if self.args.daemon:
                args.append("-d")
                subprocess.run(args)
            else:
                shell = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)

                # Ensure stdout is not None for the type checker
                if shell.stdout:
                    for line in shell.stdout:
                        if self.filter_log(line):
                            print(line, end="")

    def shell(self, *args: str) -> str:
        return subprocess.check_output(["qs", "-c", "caelestia", *args], text=True)

    def filter_log(self, line: str) -> bool:
        return f"Cannot open: file://{c_cache_dir}/imagecache/" not in line

    def print_ipc(self) -> None:
        print(self.shell("ipc", "show"), end="")

    def print_log(self) -> None:
        if self.args.log_rules:
            log = self.shell("log", "-r", self.args.log_rules)
        else:
            log = self.shell("log")
        # FIXME: remove when logging rules are added/warning is removed
        for line in log.splitlines():
            if self.filter_log(line):
                print(line)

    def message(self, *args: list[str]) -> None:
        print(self.shell("ipc", "call", *args), end="")
