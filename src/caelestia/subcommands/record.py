import re
import shutil
import subprocess
import time
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from caelestia.utils import niri
from caelestia.utils.notify import close_notification, notify
from caelestia.utils.paths import (
    get_config,
    recording_notif_path,
    recording_path,
    recording_socket_path,
    recordings_dir,
)

RECORDER = "gpu-screen-recorder"


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        if self.args.pause:
            self.pause()
        elif self.proc_running():
            self.stop()
        else:
            self.start()

    def proc_running(self) -> bool:
        return subprocess.run(["pidof", RECORDER], stdout=subprocess.DEVNULL).returncode == 0

    def intersects(self, a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]

    def fps(self) -> int | None:
        """Resolve the recording frame rate: CLI flag, then config, then display default."""
        if self.args.fps is not None:
            return self.args.fps

        config = get_config()
        try:
            refresh_rate = config["record"]["refreshRate"]
        except (KeyError, TypeError):
            return None

        if refresh_rate is None:
            return None

        try:
            return int(refresh_rate)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Config option 'record.refreshRate' should be a number: {e}")

    def gsr_cli(self) -> str | None:
        path = shutil.which("gsr-cli")
        if path is not None:
            return path
        recorder = shutil.which(RECORDER)
        if recorder is not None:
            sibling = Path(recorder).parent / "gsr-cli"
            if sibling.is_file():
                return str(sibling)
        return None

    def pause(self) -> None:
        gsr_cli = self.gsr_cli()
        if gsr_cli is not None:
            try:
                if (
                    subprocess.run(
                        [gsr_cli, "-ipc", str(recording_socket_path), "toggle-pause"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    ).returncode
                    == 0
                ):
                    return
            except OSError:
                pass
        subprocess.run(["pkill", "-USR2", "-f", RECORDER], stdout=subprocess.DEVNULL)

    def start(self) -> None:
        args = ["-w"]
        fps = self.fps()

        outputs = niri.get_outputs()
        if self.args.region:
            if self.args.region == "slurp":
                region = subprocess.check_output(
                    ["slurp", "-f", "%wx%h+%x+%y"], text=True, stdin=subprocess.DEVNULL
                )
            else:
                region = self.args.region.strip()
            args += ["region", "-region", region]

            m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", region)
            if not m:
                raise ValueError(f"Invalid region: {region}")

            w, h, x, y = map(int, m.groups())
            r = x, y, w, h
            if fps is None:
                max_rr = 0
                for name, output in outputs.items():
                    mode = output.get("mode", {})
                    loc = output.get("location", {})
                    logical = output.get("logical") or {}
                    # slurp reports logical coordinates, so intersect against
                    # the logical rectangle when available (scale-correct)
                    out_x = loc.get("x", 0)
                    out_y = loc.get("y", 0)
                    out_w = logical.get("width") or mode.get("width", 0)
                    out_h = logical.get("height") or mode.get("height", 0)
                    if self.intersects((out_x, out_y, out_w, out_h), r):
                        rr = round(mode.get("refresh_rate", 0) / 1000)
                        max_rr = max(max_rr, rr)
                fps = max_rr
            if fps and fps > 0:
                args += ["-f", str(fps)]
        else:
            focused_name = niri.get_focused_output_name()
            if not focused_name or focused_name not in outputs:
                raise ValueError("no focused output found for fullscreen recording")
            output = outputs[focused_name]
            args += [focused_name]
            if fps is None:
                fps = round(output.get("mode", {}).get("refresh_rate", 0) / 1000)
            if fps and fps > 0:
                args += ["-f", str(fps)]

        if self.args.sound:
            args += ["-a", "default_output"]

        config = get_config()
        try:
            if "record" in config and "extraArgs" in config["record"]:
                args += config["record"]["extraArgs"]
        except TypeError as e:
            raise ValueError(f"Config option 'record.extraArgs' should be an array: {e}")

        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_socket_path.unlink(missing_ok=True)
        proc = subprocess.Popen(
            [RECORDER, *args, "-ipc", str(recording_socket_path), "-o", str(recording_path)],
            start_new_session=True,
        )

        notif = notify("-p", "Recording started", "Recording...")
        recording_notif_path.write_text(notif)

        try:
            if proc.wait(1) != 0:
                close_notification(notif)
                notify(
                    "Recording failed",
                    "An error occurred attempting to start recorder. "
                    f"Command `{' '.join(proc.args)}` failed with exit code {proc.returncode}",
                )
        except subprocess.TimeoutExpired:
            pass

    def stop(self) -> None:
        # Start killing recording process
        subprocess.run(["pkill", "-f", RECORDER], stdout=subprocess.DEVNULL)

        # Wait for recording to finish to avoid corrupted video file
        while self.proc_running():
            time.sleep(0.1)

        # Move to recordings folder
        new_path = recordings_dir / f"recording_{datetime.now().strftime('%Y%m%d_%H-%M-%S')}.mp4"
        recordings_dir.mkdir(exist_ok=True, parents=True)
        shutil.move(recording_path, new_path)

        # Close start notification
        try:
            close_notification(recording_notif_path.read_text())
        except IOError:
            pass

        if self.args.clipboard:
            file_uri = Path(new_path).resolve().as_uri() + "\n"
            subprocess.run(["wl-copy", "--type", "text/uri-list"], input=file_uri.encode())

        action = notify(
            "--action=watch=Watch",
            "--action=open=Open",
            "--action=delete=Delete",
            "Recording stopped",
            f"Recording saved in {new_path}",
        )

        if action == "watch":
            subprocess.Popen(["xdg-open", new_path], start_new_session=True)
        elif action == "open":
            p = subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.freedesktop.FileManager1",
                    "--type=method_call",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:file://{new_path}",
                    "string:",
                ]
            )
            if p.returncode != 0:
                subprocess.Popen(["xdg-open", new_path.parent], start_new_session=True)
        elif action == "delete":
            new_path.unlink()
