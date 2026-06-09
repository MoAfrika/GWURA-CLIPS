import os
import shutil
import sys
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.fx.Crop import Crop
# Whisper will be imported lazily inside get_model() to avoid importing
# heavy native dependencies at module import time (which can fail on some systems).
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --- CONFIGURATION ---
UPLOAD_DIR = "uploads"
OUTPUT_DIR = "exports"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI()

# Enable CORS (Allows index.html to talk to this script)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# AI model: lazy load to avoid blocking startup and allow test runs to skip heavy downloads.
# Set environment variable GWURA_SKIP_AI=1 to disable AI entirely (useful for CI/tests).
SKIP_AI = str(os.environ.get("GWURA_SKIP_AI", "0")).lower() in ("1", "true", "yes")
model = None
AI_AVAILABLE = False

def get_model():
    """Lazily load the Whisper model on first use. Returns the model or None.
    Loading may download model weights on first run.
    """
    global model, AI_AVAILABLE, SKIP_AI
    if SKIP_AI:
        return None
    if model is None:
        try:
            print("⏳ Loading AI Model (Whisper) lazily...")
            try:
                import whisper
            except Exception as ie:
                print("⚠️ Whisper import failed:", ie)
                print("   To enable AI features install Whisper: `pip install -U openai-whisper` or use a compatible backend model.")
                AI_AVAILABLE = False
                model = None
                return None

            model = whisper.load_model("base")
            AI_AVAILABLE = True
            print("✅ AI Model Loaded. Full features enabled.")
        except Exception as e:
            print(f"⚠️ AI Model load failed: {e}")
            AI_AVAILABLE = False
            model = None
    return model

# --- LOGIC: Audio Analysis ---
def analyze_audio_energy(video_path, chunk_duration=1.0):
    """
    Scans the video's audio track to find loud/passionate moments.
    Returns a list of energy levels per second.
    """
    video = None
    clip = None
    try:
        # Fix: Load VideoFileClip first, then extract audio
        video = VideoFileClip(video_path)
        
        # Fix: Check if audio exists
        if video.audio is None:
            print("⚠️ No audio track found in video.")
            return [], video.duration

        clip = video.audio
        duration = clip.duration
        fps = clip.fps if clip.fps else 22000 # Use actual fps or fallback
        
        energies = []
        # Step through audio in 1-second chunks
        for t in np.arange(0, duration, chunk_duration):
            try:
                # Extract audio chunk
                chunk = clip.subclip(t, min(t + chunk_duration, duration)).to_soundarray(fps=fps)
                
                # Calculate RMS (Root Mean Square) volume
                if len(chunk) > 0:
                    volume = np.sqrt(np.mean(chunk**2))
                else:
                    volume = 0
                
                energies.append({"time": float(t), "energy": float(volume)})
            except Exception as chunk_err:
                # Handle edge cases in chunk extraction
                energies.append({"time": float(t), "energy": 0.0})
        
        return energies, duration
    except Exception as e:
        print(f"Audio analysis failed: {e}")
        return [], 0
    finally:
        # Fix: Ensure resources are closed
        if clip: clip.close()
        if video: video.close()

# --- ENDPOINTS ---

@app.get("/")
def health_check():
    """Frontend calls this to see if we are Online."""
    # Do not trigger model load from health check. Report current AI readiness and support.
    return {
        "status": "online",
        "ai_enabled": AI_AVAILABLE and not SKIP_AI,
        "ai_supported": not SKIP_AI,
        "ai_loaded": AI_AVAILABLE
    }

@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    """Saves the uploaded video file locally using a collision-safe filename."""
    safe_name = os.path.basename(file.filename)
    stored_filename = f"{uuid.uuid4().hex}_{safe_name}"
    file_path = os.path.join(UPLOAD_DIR, stored_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {
        "filename": stored_filename,
        "original_filename": safe_name,
        "status": "uploaded"
    }

@app.post("/analyze")
async def analyze_video(filename: str = Form(...)):
    """
    1. Transcribes audio (if AI online).
    2. Maps energy levels.
    3. Generates clips fitting your specific duration buckets.
    """
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    # 1. Energy Analysis
    energies, duration = analyze_audio_energy(file_path)
    
    # Fix: Handle empty energies (silent video or error)
    if not energies:
        return {"clips": [], "duration": duration}

    # 2. Transcription (Get text for captions)
    transcript = []
    if not SKIP_AI:
        m = get_model()
        if m:
            try:
                print(f"🎙️ Transcribing {filename}...")
                result = m.transcribe(file_path, fp16=False)
                transcript = result.get('segments', [])
            except Exception as e:
                print(f"Transcription failed: {e}")
                transcript = []
        else:
            # Model unavailable (either failed to load or skip flag set)
            transcript = []

    # 3. Smart Clip Logic
    clips = []
    
    # Calculate average energy to find peaks
    # Fix: Division by zero protection
    if len(energies) > 0:
        avg_energy = sum(e['energy'] for e in energies) / len(energies)
    else:
        avg_energy = 1.0
        
    threshold = max(1e-5, avg_energy * 1.1)  # 10% above average is interesting
    
    # Clip Types Definitions used for labels and preferred duration ranges.
    # Actual clip length is driven by the audio energy plateau around the peak,
    # but still capped to 4 minutes.
    CLIP_TYPES = [
        {"label": "Viral Short", "min": 30, "max": 90},
        {"label": "Mini Sermon", "min": 45, "max": 150},
        {"label": "Teaching Block", "min": 90, "max": 210},
        {"label": "Full Message", "min": 120, "max": 240}
    ]

    # Find potential start points (High energy).
    # Sort by highest energy first to get the most interesting clips.
    sorted_peaks = sorted(energies, key=lambda x: x['energy'], reverse=True)
    sorted_peaks = sorted_peaks[:min(len(sorted_peaks), 250)]
    energy_by_time = sorted(energies, key=lambda x: x['time'])
    generated_count = 0
    
    for peak in sorted_peaks:
        if generated_count >= 50: break # Hard limit
        
        t_center = peak['time']
        
        # Check overlap with existing clips
        is_overlapping = False
        for c in clips:
            if c['start'] < t_center < c['end']:
                is_overlapping = True
                break
        if is_overlapping: continue
        
        # Skip low energy
        if peak['energy'] < threshold: continue

        # Determine Clip Length & Type
        type_def = CLIP_TYPES[min(generated_count, len(CLIP_TYPES) - 1)]

        # Use the local energy plateau around the peak to vary clip duration.
        peak_idx = None
        if energy_by_time:
            peak_idx = min(range(len(energy_by_time)), key=lambda i: abs(energy_by_time[i]['time'] - t_center))
        plateau_start = peak_idx
        plateau_end = peak_idx
        plateau_threshold = max(peak['energy'] * 0.35, threshold * 0.55)
        if peak_idx is not None:
            while plateau_start > 0 and energy_by_time[plateau_start - 1]['energy'] >= plateau_threshold:
                plateau_start -= 1
            while plateau_end < len(energy_by_time) - 1 and energy_by_time[plateau_end + 1]['energy'] >= plateau_threshold:
                plateau_end += 1

        # Compute a content-driven duration.
        plateau_start_time = energy_by_time[plateau_start]['time'] if peak_idx is not None else max(0, t_center - 15)
        plateau_end_time = min(duration, energy_by_time[plateau_end]['time'] + 1.0) if peak_idx is not None else min(duration, t_center + 15)
        plateau_len = plateau_end_time - plateau_start_time

        if plateau_len >= type_def['min']:
            chosen_len = min(plateau_len, type_def['max'], 240)
        else:
            chosen_len = min(240, max(type_def['min'], plateau_len * 3, 60))

        # Use the plateau if it captures meaningful context, otherwise center the clip.
        if plateau_len >= type_def['min']:
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

        # If the chosen duration is still shorter than the label preference, extend it slightly.
        if end - start < type_def['min']:
            extend = type_def['min'] - (end - start)
            start = max(0, start - extend / 2)
            end = min(duration, end + extend / 2)
            if end - start > 240:
                end = start + 240

        # Get Caption Preview
        caption_text = "Type caption here..."
        if AI_AVAILABLE and transcript:
            # Find the text segment happening at the start of the clip
            for seg in transcript:
                if seg['start'] <= t_center <= seg['end']:
                    caption_text = seg['text'].strip()
                    break

        clips.append({
            "id": generated_count + 1,
            "title": f"Clip {generated_count + 1}: {type_def['label']}",
            "type": type_def['label'],
            "start": start,
            "end": end,
            "duration": end - start,
            "score": int(min((peak['energy'] / (avg_energy or 1)) * 70 + 20, 99)),
            "caption_preview": caption_text
        })
        generated_count += 1

    # Sort clips by time so they appear in order in the sidebar
    clips.sort(key=lambda x: x['start'])
    
    return {"clips": clips, "duration": duration}

@app.post("/render")
async def render_clip(
    filename: str = Form(...),
    start: float = Form(...),
    end: float = Form(...),
    pan_x: float = Form(...),
    caption: str = Form(...)
):
    """
    Renders the final video using FFmpeg.
    - Crops to 9:16
    - Encodes to MP4 (AAC Audio)
    - High Quality
    """
    input_path = os.path.join(UPLOAD_DIR, filename)
    output_filename = f"gwura_clip_{uuid.uuid4().hex}_{int(start)}_{int(end)}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    video = None
    final_clip = None

    try:
        print(f"🎬 Rendering {output_filename}...")
        
        # Load Video Subclip
        video = VideoFileClip(input_path).subclip(start, end)
        
        # Normalize pan input and clamp to valid range
        pan_x = min(max(pan_x, 0.0), 1.0)

        # Calculate Crop for 9:16 (720x1280)
        src_w, src_h = video.size
        target_ratio = 9 / 16
        x1 = 0
        y1 = 0

        if src_w / src_h > target_ratio:
            # Source is wider than target: crop width
            new_w = src_h * target_ratio
            max_x = src_w - new_w
            x1 = max(0, min(max_x, max_x * pan_x))
            new_h = src_h
        else:
            # Source is narrower or tall: crop height
            new_h = src_w / target_ratio
            y1 = max(0, min(src_h - new_h, (src_h - new_h) * 0.5))
            new_w = src_w

        # Use the installed Crop Effect class and apply it to the clip
        final_clip = Crop(x1=int(x1), y1=int(y1), width=int(new_w), height=int(new_h)).apply(video)
        
        # Resize to 720x1280 (Standard HD Short)
        final_clip = final_clip.resize(width=720, height=1280)
        
        # Write
        final_clip.write_videofile(
            output_path, 
            codec="libx264", 
            audio_codec="aac", 
            bitrate="5000k",
            preset="fast",
            threads=4,
            logger=None
        )
        
        return FileResponse(output_path, media_type="video/mp4", filename=output_filename)

    except Exception as e:
        print(f"Render Error: {e}")
        raise HTTPException(status_code=500, detail=f"Render Failed: {str(e)}")
    finally:
        # Fix: Ensure clips are closed to prevent memory leaks/file locks
        if final_clip:
            try: final_clip.close()
            except: pass
        if video:
            try: video.close()
            except: pass

if __name__ == "__main__":
    import uvicorn
    print("🚀 GWURA BACKEND STARTING...")
    print("👉 Checking for AI Model...")
    port = int(os.environ.get("PORT", "5501"))
    uvicorn.run(app, host="0.0.0.0", port=port)
