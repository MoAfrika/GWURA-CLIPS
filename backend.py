import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from moviepy.video.fx.Crop import Crop
from moviepy.video.io.VideoFileClip import VideoFileClip

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploads"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", BASE_DIR / "exports"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "2048")) * 1024 * 1024

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# AI model: lazy load to avoid blocking startup and allow test runs to skip heavy downloads.
SKIP_AI = str(os.environ.get("GWURA_SKIP_AI", "0")).lower() in ("1", "true", "yes")
OPENAI_TRANSCRIBE_MODEL = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
model = None
AI_AVAILABLE = False
AI_LAST_ERROR = ""


def _status_payload():
    if SKIP_AI:
        transcription_backend = "disabled"
    elif os.environ.get("OPENAI_API_KEY"):
        transcription_backend = f"openai-api:{OPENAI_TRANSCRIBE_MODEL}"
    else:
        transcription_backend = "local-whisper"

    return {
        "status": "online",
        "ai_enabled": (AI_AVAILABLE or bool(os.environ.get("OPENAI_API_KEY"))) and not SKIP_AI,
        "ai_supported": not SKIP_AI,
        "ai_loaded": AI_AVAILABLE,
        "ai_backend": transcription_backend,
        "ai_error": AI_LAST_ERROR,
    }


def _safe_upload_name(filename: Optional[str]) -> str:
    name = (filename or "upload.mp4").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "upload.mp4"


def _resolve_upload(filename: str) -> Path:
    candidate = (UPLOAD_DIR / _safe_upload_name(filename)).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in candidate.parents and candidate != upload_root:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return candidate


def _subclip(clip, start, end):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    return clip.subclip(start, end)


def _resize(clip, width=None, height=None):
    if hasattr(clip, "resized"):
        return clip.resized(width=width, height=height)
    return clip.resize(width=width, height=height)


def get_model():
    """Lazily load local Whisper on first use."""
    global model, AI_AVAILABLE, AI_LAST_ERROR
    if SKIP_AI or os.environ.get("OPENAI_API_KEY"):
        return None

    if model is None:
        try:
            print("Loading local AI model (Whisper) lazily...")
            try:
                import whisper
            except Exception as exc:
                AI_LAST_ERROR = str(exc)
                print("Whisper import failed:", exc)
                print("Install local AI with: pip uninstall whisper && pip install -U openai-whisper")
                AI_AVAILABLE = False
                return None

            if not hasattr(whisper, "load_model"):
                AI_LAST_ERROR = "Installed `whisper` package does not expose load_model. Install `openai-whisper`."
                print(AI_LAST_ERROR)
                AI_AVAILABLE = False
                return None

            model = whisper.load_model(os.environ.get("WHISPER_MODEL", "base"))
            AI_AVAILABLE = True
            AI_LAST_ERROR = ""
            print("AI model loaded. Full transcription features enabled.")
        except Exception as exc:
            AI_LAST_ERROR = str(exc)
            print(f"AI model load failed: {exc}")
            AI_AVAILABLE = False
            model = None
    return model


def _normalize_segments(segments):
    normalized = []
    for seg in segments or []:
        try:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start + 3))
            normalized.append({"start": start, "end": max(end, start + 0.25), "text": text})
        except Exception:
            continue
    return normalized


def _object_to_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}) or {})


def transcribe_video(video_path: Path):
    global AI_LAST_ERROR
    if SKIP_AI:
        return []

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI

            client = OpenAI()
            print(f"Transcribing with OpenAI API ({OPENAI_TRANSCRIBE_MODEL})...")
            with open(video_path, "rb") as audio_file:
                result = client.audio.transcriptions.create(
                    model=OPENAI_TRANSCRIBE_MODEL,
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            AI_LAST_ERROR = ""
            return _normalize_segments(_object_to_dict(result).get("segments", []))
        except Exception as exc:
            AI_LAST_ERROR = f"OpenAI transcription failed: {exc}"
            print(AI_LAST_ERROR)
            return []

    local_model = get_model()
    if not local_model:
        return []

    try:
        print(f"Transcribing locally with Whisper: {video_path.name}...")
        result = local_model.transcribe(str(video_path), fp16=False)
        AI_LAST_ERROR = ""
        return _normalize_segments(result.get("segments", []))
    except Exception as exc:
        AI_LAST_ERROR = f"Local transcription failed: {exc}"
        print(AI_LAST_ERROR)
        return []


# --- LOGIC: Audio Analysis ---
def analyze_audio_energy(video_path, chunk_duration=1.0):
    """Scan the video's audio track and return energy levels per second."""
    video = None
    clip = None
    try:
        video = VideoFileClip(str(video_path))

        if video.audio is None:
            print("No audio track found in video.")
            return [], video.duration

        clip = video.audio
        duration = clip.duration
        fps = clip.fps if clip.fps else 22000

        energies = []
        for t in np.arange(0, duration, chunk_duration):
            try:
                chunk = _subclip(clip, t, min(t + chunk_duration, duration)).to_soundarray(fps=fps)
                volume = np.sqrt(np.mean(chunk**2)) if len(chunk) > 0 else 0
                energies.append({"time": float(t), "energy": float(volume)})
            except Exception:
                energies.append({"time": float(t), "energy": 0.0})

        return energies, duration
    except Exception as exc:
        print(f"Audio analysis failed: {exc}")
        return [], 0
    finally:
        if clip:
            clip.close()
        if video:
            video.close()


# --- FRONTEND + HEALTH ---
@app.get("/")
def root(request: Request):
    """Serve the app to browsers and JSON health to fetch probes."""
    accept = request.headers.get("accept", "")
    index_path = BASE_DIR / "index.html"
    if "text/html" in accept and index_path.exists():
        return FileResponse(index_path)
    return _status_payload()


@app.get("/health")
def health_check():
    return _status_payload()


@app.get("/app.js", include_in_schema=False)
def frontend_js():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript")


@app.get("/style.css", include_in_schema=False)
def frontend_css():
    return FileResponse(BASE_DIR / "style.css", media_type="text/css")


@app.get("/logo.png", include_in_schema=False)
def frontend_logo():
    return FileResponse(BASE_DIR / "logo.png", media_type="image/png")


# --- API ENDPOINTS ---
@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    safe_name = _safe_upload_name(file.filename)
    stored_filename = f"{uuid.uuid4().hex}_{safe_name}"
    file_path = _resolve_upload(stored_filename)
    bytes_written = 0

    with open(file_path, "wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > MAX_UPLOAD_BYTES:
                buffer.close()
                file_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
            buffer.write(chunk)

    return {
        "filename": stored_filename,
        "original_filename": safe_name,
        "bytes": bytes_written,
        "status": "uploaded",
    }


@app.post("/analyze")
async def analyze_video(filename: str = Form(...)):
    """
    1. Transcribes audio when AI is configured.
    2. Maps energy levels.
    3. Generates clips and timed caption rows for the editor/exporter.
    """
    file_path = _resolve_upload(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    energies, duration = analyze_audio_energy(file_path)
    if not energies:
        return {"clips": [], "duration": duration, "transcript": [], "ai": _status_payload()}

    transcript = transcribe_video(file_path)
    clips = []

    avg_energy = sum(e["energy"] for e in energies) / len(energies) if energies else 1.0
    threshold = max(1e-5, avg_energy * 1.1)

    clip_types = [
        {"label": "Viral Short", "min": 30, "max": 90},
        {"label": "Mini Sermon", "min": 45, "max": 150},
        {"label": "Teaching Block", "min": 90, "max": 210},
        {"label": "Full Message", "min": 120, "max": 240},
    ]

    sorted_peaks = sorted(energies, key=lambda x: x["energy"], reverse=True)
    sorted_peaks = sorted_peaks[: min(len(sorted_peaks), 250)]
    energy_by_time = sorted(energies, key=lambda x: x["time"])
    generated_count = 0

    for peak in sorted_peaks:
        if generated_count >= 50:
            break

        t_center = peak["time"]
        if any(c["start"] < t_center < c["end"] for c in clips):
            continue
        if peak["energy"] < threshold:
            continue

        type_def = clip_types[min(generated_count, len(clip_types) - 1)]
        peak_idx = min(range(len(energy_by_time)), key=lambda i: abs(energy_by_time[i]["time"] - t_center))
        plateau_start = peak_idx
        plateau_end = peak_idx
        plateau_threshold = max(peak["energy"] * 0.35, threshold * 0.55)

        while plateau_start > 0 and energy_by_time[plateau_start - 1]["energy"] >= plateau_threshold:
            plateau_start -= 1
        while plateau_end < len(energy_by_time) - 1 and energy_by_time[plateau_end + 1]["energy"] >= plateau_threshold:
            plateau_end += 1

        plateau_start_time = energy_by_time[plateau_start]["time"]
        plateau_end_time = min(duration, energy_by_time[plateau_end]["time"] + 1.0)
        plateau_len = plateau_end_time - plateau_start_time

        if plateau_len >= type_def["min"]:
            chosen_len = min(plateau_len, type_def["max"], 240)
        else:
            chosen_len = min(240, max(type_def["min"], plateau_len * 3, 60))

        if plateau_len >= type_def["min"]:
            start = plateau_start_time
            end = plateau_end_time
            if end - start > chosen_len:
                start = max(0, t_center - chosen_len / 2)
                end = min(duration, start + chosen_len)
        else:
            start = max(0, t_center - chosen_len / 2)
            end = min(duration, start + chosen_len)
            if end == duration:
                start = max(0, end - chosen_len)

        if end - start < type_def["min"]:
            extend = type_def["min"] - (end - start)
            start = max(0, start - extend / 2)
            end = min(duration, end + extend / 2)
            if end - start > 240:
                end = start + 240

        clip_transcript = [seg for seg in transcript if seg["end"] >= start and seg["start"] <= end]
        caption_text = clip_transcript[0]["text"] if clip_transcript else "Type caption here..."

        clips.append(
            {
                "id": generated_count + 1,
                "title": f"Clip {generated_count + 1}: {type_def['label']}",
                "type": type_def["label"],
                "start": start,
                "end": end,
                "duration": end - start,
                "score": int(min((peak["energy"] / (avg_energy or 1)) * 70 + 20, 99)),
                "caption_preview": caption_text,
                "captions": clip_transcript,
            }
        )
        generated_count += 1

    clips.sort(key=lambda x: x["start"])
    return {"clips": clips, "duration": duration, "transcript": transcript, "ai": _status_payload()}


def _ass_time(seconds):
    seconds = max(0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int((seconds - int(seconds)) * 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape_ass_text(text):
    return str(text).replace("\\", "\\\\").replace("{", "").replace("}", "").replace("\n", "\\N")


def _parse_caption_segments(captions_json, caption, start, end):
    segments = []
    if captions_json:
        try:
            raw = json.loads(captions_json)
            for item in raw:
                text = str(item.get("text", "")).strip()
                if not text or text == "Type caption here...":
                    continue
                seg_start = float(item.get("time", item.get("start", start)))
                seg_end = float(item.get("end", seg_start + 3))
                seg_start = max(start, min(end, seg_start))
                seg_end = max(seg_start + 0.25, min(end, seg_end))
                segments.append({"start": seg_start - start, "end": seg_end - start, "text": text})
        except Exception as exc:
            print(f"Caption JSON parse failed: {exc}")

    if not segments and caption and caption.strip():
        segments.append({"start": 0, "end": max(0.5, end - start), "text": caption.strip()})
    return segments


def _write_ass_file(path: Path, segments):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,58,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,54,54,210,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for seg in segments:
        lines.append(
            f"Dialogue: 0,{_ass_time(seg['start'])},{_ass_time(seg['end'])},Default,,0,0,0,,{_escape_ass_text(seg['text'])}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def _burn_captions(input_path: Path, output_path: Path, segments):
    if not segments:
        shutil.move(str(input_path), str(output_path))
        return

    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        shutil.move(str(input_path), str(output_path))
        print("FFmpeg executable not found for caption burn-in; exported video without subtitles.")
        return

    ass_path = OUTPUT_DIR / f"captions_{uuid.uuid4().hex}.ass"
    _write_ass_file(ass_path, segments)
    rel_ass = os.path.relpath(ass_path, BASE_DIR).replace("\\", "/").replace("'", "\\'")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(input_path),
                "-vf",
                f"subtitles='{rel_ass}'",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-c:a",
                "copy",
                str(output_path),
            ],
            cwd=BASE_DIR,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        ass_path.unlink(missing_ok=True)


@app.post("/render")
async def render_clip(
    filename: str = Form(...),
    start: float = Form(...),
    end: float = Form(...),
    pan_x: float = Form(...),
    caption: str = Form(""),
    captions_json: str = Form(""),
):
    input_path = _resolve_upload(filename)
    if not input_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    output_filename = f"gwura_clip_{uuid.uuid4().hex}_{int(start)}_{int(end)}.mp4"
    output_path = OUTPUT_DIR / output_filename
    temp_output_path = OUTPUT_DIR / f"raw_{output_filename}"

    video = None
    final_clip = None

    try:
        print(f"Rendering {output_filename}...")
        video = _subclip(VideoFileClip(str(input_path)), start, end)
        pan_x = min(max(pan_x, 0.0), 1.0)

        src_w, src_h = video.size
        target_ratio = 9 / 16
        x1 = 0
        y1 = 0

        if src_w / src_h > target_ratio:
            new_w = src_h * target_ratio
            max_x = src_w - new_w
            x1 = max(0, min(max_x, max_x * pan_x))
            new_h = src_h
        else:
            new_h = src_w / target_ratio
            y1 = max(0, min(src_h - new_h, (src_h - new_h) * 0.5))
            new_w = src_w

        final_clip = Crop(x1=int(x1), y1=int(y1), width=int(new_w), height=int(new_h)).apply(video)
        final_clip = _resize(final_clip, width=720, height=1280)

        final_clip.write_videofile(
            str(temp_output_path),
            codec="libx264",
            audio_codec="aac",
            bitrate="5000k",
            preset="fast",
            threads=4,
            logger=None,
        )

        caption_segments = _parse_caption_segments(captions_json, caption, start, end)
        _burn_captions(temp_output_path, output_path, caption_segments)

        return FileResponse(str(output_path), media_type="video/mp4", filename=output_filename)

    except Exception as exc:
        print(f"Render Error: {exc}")
        raise HTTPException(status_code=500, detail=f"Render Failed: {str(exc)}")
    finally:
        temp_output_path.unlink(missing_ok=True)
        if final_clip:
            try:
                final_clip.close()
            except Exception:
                pass
        if video:
            try:
                video.close()
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn

    print("GWURA BACKEND STARTING...")
    print("Open this app at the server URL shown below.")
    port = int(os.environ.get("PORT", "5501"))
    uvicorn.run(app, host="0.0.0.0", port=port)
