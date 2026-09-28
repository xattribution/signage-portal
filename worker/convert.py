"""
Conversion worker.

This is the only component that parses untrusted files (PowerPoint, PDF, video, images).
It runs in its own container with:
  * no network
  * no access to the database, secret key, or the served media folder
  * read-only view of the uploaded originals, write access only to its work folder
  * a read-only root filesystem, no Linux capabilities, and resource limits
  * per-process CPU / memory / file-size limits on every converter it launches

Contract with the web container (files only, nothing else is shared):
  ORIGINALS/<id>.job.json      written by web: {"id", "job_token", "source", "kind", "target_w", "target_h", "crf"}
  ORIGINALS/<source>           the uploaded file
  WORK/<id>/result.json        written here on success: {"job_token", "slides": [...]}
  WORK/<id>/error.json         written here on failure: {"job_token", "error": "..."}
Output is assembled in WORK/.tmp-<id> and renamed into place, so the web side never
sees a half-written job.
"""
import json
import math
import tempfile
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
import traceback
import warnings
from pathlib import Path

from PIL import Image, ImageOps

ORIGINALS = Path(os.getenv("ORIGINALS_DIR", "/media/originals"))
WORK = Path(os.getenv("WORK_DIR", "/media/work"))
TMP = Path(os.getenv("TMPDIR", "/tmp"))
POLL = float(os.getenv("WORKER_POLL_SECONDS", "2"))
MAX_WALL = int(os.getenv("CONVERT_WALL_SECONDS", "3600"))
MAX_TOTAL = int(os.getenv("CONVERT_MAX_OUTPUT_MB", "8192")) * 1048576
MAX_LOG = 1048576

MAX_SLIDES = 500
THUMB_W = 480

# Converter limits (per child process)
LIM_MEM = int(os.getenv("CONVERT_MEM_MB", "3072")) * 1024 * 1024
LIM_CPU = int(os.getenv("CONVERT_CPU_SECONDS", "3600"))
LIM_FSIZE = int(os.getenv("CONVERT_MAX_OUTPUT_MB", "8192")) * 1024 * 1024

# Decompression-bomb guard: refuse images over ~120 MP instead of just warning.
Image.MAX_IMAGE_PIXELS = 120_000_000
warnings.simplefilter("error", Image.DecompressionBombWarning)

JOB_RE = re.compile(r"^(\d{1,9})\.job\.json$")
SOURCE_RE = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,5}$")

# Only real container formats are accepted for video. This blocks playlist/concat
# style inputs (HLS, concat, image2 sequences) that can be abused to read other files.
VIDEO_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi", "asf", "mpeg", "mpegts"}
PROBE_SAFE = ["-protocol_whitelist", "file", "-format_whitelist", ",".join(sorted(VIDEO_FORMATS))]
FFMPEG_SAFE = ["-nostdin", *PROBE_SAFE, "-threads", "2", "-filter_threads", "1"]

# LibreOffice: macros off, no remote content, no auto-updates, in a throwaway profile.
LO_REGISTRY = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="UseOpenCL" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Load"><prop oor:name="UpdateDocMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""


class JobError(Exception):
    pass


# ---------------------------------------------------------------------------
def _limits():
    resource.setrlimit(resource.RLIMIT_AS, (LIM_MEM, LIM_MEM))
    resource.setrlimit(resource.RLIMIT_CPU, (LIM_CPU, LIM_CPU))
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIM_FSIZE, LIM_FSIZE))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))



def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    # File-backed, capped diagnostics avoid unbounded capture_output allocations.
    # The supervisor kills the *whole job process group*, including grandchildren.
    with tempfile.TemporaryFile(dir=TMP) as stdout, tempfile.TemporaryFile(dir=TMP) as stderr:
        p = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                             env=_child_env(), preexec_fn=_limits)
        deadline = time.monotonic() + timeout
        try:
            while p.poll() is None:
                if time.monotonic() > deadline:
                    raise JobError(f"{Path(cmd[0]).name} timed out")
                if os.fstat(stdout.fileno()).st_size > MAX_LOG or os.fstat(stderr.fileno()).st_size > MAX_LOG:
                    raise JobError(f"{Path(cmd[0]).name} produced too much diagnostic output")
                time.sleep(0.1)
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
        stdout.seek(0); stderr.seek(0)
        output = stdout.read(MAX_LOG).decode("utf-8", "replace")
        errors = stderr.read(MAX_LOG).decode("utf-8", "replace")
        if p.returncode:
            # Raw converter diagnostics stay in worker logs, not browser error text.
            print(errors[-1000:], file=sys.stderr, flush=True)
            raise JobError(f"{Path(cmd[0]).name} could not convert this file.")
        return subprocess.CompletedProcess(cmd, p.returncode, output, errors)


def _child_env() -> dict:
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(TMP), "TMPDIR": str(TMP), "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}


# ---------------------------------------------------------------------------
def fit_on_black(img: Image.Image, W: int, H: int) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    fitted = ImageOps.contain(img, (W, H), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(fitted, ((W - fitted.width) // 2, (H - fitted.height) // 2))
    return canvas


def save_pair(img: Image.Image, out: Path, idx: int) -> dict:
    full, thumb = f"{idx:03d}.jpg", f"{idx:03d}_t.jpg"
    img.save(out / full, "JPEG", quality=90, optimize=True, progressive=True)
    t = img.copy()
    t.thumbnail((THUMB_W, THUMB_W))
    t.save(out / thumb, "JPEG", quality=80)
    return {"idx": idx, "kind": "image", "file": full, "thumb": thumb}


def render_image(src: Path, out: Path, W: int, H: int) -> list[dict]:
    try:
        with Image.open(src) as im:
            im.seek(0)
            return [save_pair(fit_on_black(im, W, H), out, 0)]
    except Image.DecompressionBombWarning as e:
        raise JobError("Image is too large.") from e
    except Image.DecompressionBombError as e:
        raise JobError("Image is too large.") from e
    except (OSError, SyntaxError, ValueError) as e:
        raise JobError("Not a readable image.") from e


def probe(path: Path) -> dict:
    p = run(["ffprobe", *PROBE_SAFE, "-v", "error", "-show_entries",
             "format=format_name,duration:stream=codec_type", "-of", "json", str(path)], 120)
    return json.loads(p.stdout)


def render_video(src: Path, out: Path, W: int, H: int, crf: int) -> list[dict]:
    info = probe(src)
    fmts = set((info.get("format", {}).get("format_name") or "").split(","))
    if not fmts & VIDEO_FORMATS:
        raise JobError("Unsupported video container.")
    if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
        raise JobError("No video track found.")

    source_duration = float(info.get("format", {}).get("duration", 0))
    if not math.isfinite(source_duration) or source_duration > 14400:
        raise JobError("Video must be no longer than four hours.")
    dest = out / "000.mp4"
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p")
    run(["ffmpeg", *FFMPEG_SAFE, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn",
         "-vf", vf, "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
         "-profile:v", "high", "-level", "5.1" if W > 1920 or H > 1080 else "4.1", "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
         "-movflags", "+faststart", "-t", "14400", str(dest)], 4 * 3600)

    dur = float(probe(dest)["format"]["duration"])
    if not math.isfinite(dur) or not 0 < dur <= 14400:
        raise JobError("Invalid converted video duration.")
    thumb = out / "000_t.jpg"
    at = max(0.0, min(1.0, dur / 2))
    run(["ffmpeg", *FFMPEG_SAFE, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(dest),
         "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2", str(thumb)], 120)
    return [{"idx": 0, "kind": "video", "file": "000.mp4", "thumb": "000_t.jpg", "duration_ms": int(round(dur * 1000))}]


def render_deck(src: Path, out: Path, scratch: Path, W: int, H: int) -> list[dict]:
    if src.suffix.lower() == ".pdf":
        pdf = src
    else:
        profile = scratch / "lo_profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(LO_REGISTRY)
        conv = scratch / "pdf"
        conv.mkdir()
        # Copy in under a fixed, harmless name so nothing in the original filename reaches the command line.
        local = scratch / f"deck{src.suffix.lower()}"
        shutil.copyfile(src, local)
        run(["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
             "--nolockcheck", "--nodefault", "--convert-to", "pdf", "--outdir", str(conv), str(local)], 15 * 60)
        pdfs = list(conv.glob("*.pdf"))
        if not pdfs:
            raise JobError("Could not convert this presentation.")
        pdf = pdfs[0]

    info = run(["pdfinfo", str(pdf)], 60).stdout
    count_match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    count = int(count_match.group(1)) if count_match else 0
    if not 1 <= count <= MAX_SLIDES:
        raise JobError(f"Documents must contain 1–{MAX_SLIDES} pages.")
    slides = []
    used = 0
    # Rasterize one bounded page at a time instead of filling tmpfs with 500 PNGs.
    for page in range(1, count + 1):
        prefix = scratch / "page"
        run(["pdftoppm", "-png", "-f", str(page), "-l", str(page), "-singlefile",
             "-scale-to", str(max(W, H)), str(pdf), str(prefix)], 120)
        raster = prefix.with_suffix(".png")
        with Image.open(raster) as im:
            slide = save_pair(fit_on_black(im, W, H), out, page - 1)
        raster.unlink()
        used += sum((out / slide[k]).stat().st_size for k in ("file", "thumb"))
        if used > MAX_TOTAL:
            raise JobError("Document output exceeds the storage budget.")
        slides.append(slide)
    return slides


# ---------------------------------------------------------------------------
def load_job(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:
        raise JobError("Invalid job ticket.")
    job = json.loads(path.read_text())
    match = JOB_RE.fullmatch(path.name)
    if not isinstance(job, dict) or not match or type(job.get("id")) is not int or job["id"] != int(match.group(1)):
        raise JobError("Invalid job identity.")
    source, token = job.get("source"), job.get("job_token")
    if not isinstance(source, str) or not SOURCE_RE.fullmatch(source):
        raise JobError("Invalid job source name.")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise JobError("Invalid conversion generation.")
    kind = job.get("kind")
    if kind not in ("image", "video", "deck", "graphic"):
        raise JobError("Invalid job kind.")
    W, H, crf = job.get("target_w"), job.get("target_h"), job.get("crf")
    if any(type(v) is not int for v in (W, H, crf)) or not (320 <= W <= 3840 and 240 <= H <= 2160 and 14 <= crf <= 35):
        raise JobError("Invalid conversion settings.")
    if W % 2 or H % 2:
        raise JobError("Video dimensions must be even.")
    return {"id": job["id"], "source": source, "job_token": token, "kind": kind, "W": W, "H": H, "crf": crf}


def _staging(job: dict) -> Path:
    return WORK / f".tmp-{job['id']}-{job['job_token']}"


def _fail(staging: Path, msg: str, job: dict) -> None:
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    (staging / "error.json").write_text(json.dumps({"job_token": job["job_token"], "error": msg[:300]}))
    print(f"[worker] job {job['id']}: {msg}", flush=True)


def process(job_path: Path, expected_token: str) -> None:
    job = load_job(job_path)
    if job["job_token"] != expected_token:
        return
    staging = _staging(job)
    scratch = TMP / f"job-{job['id']}-{job['job_token']}"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)
    staging.mkdir(parents=True)
    scratch.mkdir(parents=True)
    try:
        src = ORIGINALS / job["source"]
        if src.is_symlink() or not src.is_file():
            raise JobError("Uploaded file is missing.")
        if job['kind'] == 'graphic':
            from graphics import render_graphic
            try:
                slides = render_graphic(src, staging)
            except ValueError as exc:
                raise JobError(str(exc)) from exc
        elif job["kind"] == "image":
            slides = render_image(src, staging, job["W"], job["H"])
        elif job["kind"] == "video":
            slides = render_video(src, staging, job["W"], job["H"], job["crf"])
        else:
            slides = render_deck(src, staging, scratch, job["W"], job["H"])
        if sum(p.stat().st_size for p in staging.iterdir()) > MAX_TOTAL:
            raise JobError("Converted output exceeds the storage budget.")
        (staging / "result.json").write_text(json.dumps({"job_token": job["job_token"], "slides": slides}))
        print(f"[worker] job {job['id']}: {len(slides)} slide(s)", flush=True)
    except JobError as exc:
        _fail(staging, str(exc), job)
    except Exception:
        traceback.print_exc()
        _fail(staging, "Conversion failed. Check the worker logs or re-export the source file.", job)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise(job_path: Path) -> None:
    job = load_job(job_path)
    staging = _staging(job)
    # The supervisor never parses media and remains responsive during conversion.
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--job", str(job_path), job["job_token"]],
                         start_new_session=True, stdin=subprocess.DEVNULL)
    deadline = time.monotonic() + MAX_WALL
    obsolete = False
    failure = None
    try:
        while p.poll() is None:
            (WORK / ".heartbeat").touch()
            try:
                obsolete = load_job(job_path)["job_token"] != job["job_token"]
            except (OSError, ValueError, JobError):
                obsolete = True
            if obsolete:
                break
            if time.monotonic() >= deadline:
                failure = "Conversion exceeded the time limit."
                break
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    finally:
        # Always reap grandchildren, even after a successful wrapper exits.
        _kill_group(p)
    if obsolete:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(TMP / f"job-{job['id']}-{job['job_token']}", ignore_errors=True)
        return
    if failure or p.returncode != 0:
        _fail(staging, failure or "Converter exceeded its resource limits or stopped unexpectedly.", job)
    try:
        if load_job(job_path)["job_token"] != job["job_token"]:
            return
        final = WORK / str(job["id"])
        shutil.rmtree(final, ignore_errors=True)
        staging.rename(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(TMP / f"job-{job['id']}-{job['job_token']}", ignore_errors=True)


def pending() -> list[Path]:
    return sorted((p for p in ORIGINALS.glob("*.job.json") if JOB_RE.fullmatch(p.name)
                   and not p.is_symlink() and not (WORK / p.name.split(".")[0]).exists()),
                  key=lambda p: int(p.name.split(".")[0]))


def main() -> None:
    from storage_check import verify
    verify()
    os.umask(0o027)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    WORK.mkdir(parents=True, exist_ok=True)
    for stale in WORK.glob(".tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)
    for stale in TMP.glob("job-*-*"):
        if re.fullmatch(r"job-[0-9]+-[0-9a-f]{32}", stale.name):
            shutil.rmtree(stale, ignore_errors=True)
    while True:
        (WORK / ".heartbeat").touch()
        for job in pending():
            try:
                supervise(job)
            except Exception:
                traceback.print_exc()
        time.sleep(POLL)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--job":
        _limits()  # Pillow, not just external tools, now has per-job resource limits.
        process(Path(sys.argv[2]), sys.argv[3])
    else:
        main()
