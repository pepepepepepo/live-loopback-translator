"""
voice_translator.py — Real-time English to Japanese translator
==============================================================
Detects speech start/end automatically using VAD,
then transcribes and translates each utterance.

Usage:
    python voice_translator.py           # Microphone input
    python voice_translator.py --stereo  # System audio (YouTube, meetings, etc.)
    python voice_translator.py --list    # List audio devices

Controls:
    Ctrl+C - Quit

How it works:
    1. Monitors audio in 30ms chunks continuously
    2. VAD detects speech start -> buffer accumulates audio
    3. 0.8s silence -> end of utterance -> Whisper -> Ollama translation -> display
"""

from __future__ import annotations

import sys
import argparse
import threading
import queue
from datetime import datetime

import numpy as np
import pyaudiowpatch as pyaudio
import webrtcvad
from faster_whisper import WhisperModel
import requests

# ── Configuration ────────────────────────────────────────
SAMPLE_RATE        = 16000      # Required by VAD and Whisper
FRAME_DURATION     = 30         # ms (VAD supports 10/20/30ms only)
FRAME_SIZE         = int(SAMPLE_RATE * FRAME_DURATION / 1000)  # 480 samples
VAD_AGGRESSIVENESS = 2          # 0 (lenient) to 3 (strict)
SILENCE_THRESHOLD  = 0.8        # seconds of silence to end an utterance
MIN_SPEECH_SECS    = 0.5        # ignore utterances shorter than this
MAX_SPEECH_SECS    = 7.0        # force flush after this many seconds (no silence)
WHISPER_MODEL      = "small.en"
OLLAMA_URL         = "http://localhost:11434/api/generate"
TRANSLATE_MODEL    = "qwen3.5:9b"  # change to any model available in your Ollama

# ── Argument parsing ─────────────────────────────────────
parser = argparse.ArgumentParser(
    description="Real-time English → Japanese voice translator",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  python voice_translator.py --stereo          # translate YouTube / system audio
  python voice_translator.py --stereo --max 5  # force translate every 5s
  python voice_translator.py --vad 3           # stricter VAD (less noise pickup)
""",
)
parser.add_argument("--stereo", action="store_true",
                    help="Use WASAPI loopback (system audio: YouTube, meetings, etc.)")
parser.add_argument("--list",   action="store_true",
                    help="List audio devices and exit")
parser.add_argument("--vad",    type=int, default=VAD_AGGRESSIVENESS,
                    choices=[0, 1, 2, 3],
                    help="VAD aggressiveness 0=lenient .. 3=strict (default: 2)")
parser.add_argument("--max",    type=float, default=MAX_SPEECH_SECS,
                    help=f"Max buffer seconds before forced translation (default: {MAX_SPEECH_SECS})")
parser.add_argument("--model",  type=str, default=TRANSLATE_MODEL,
                    help=f"Ollama model for translation (default: {TRANSLATE_MODEL})")
parser.add_argument("--ollama", type=str, default=OLLAMA_URL,
                    help=f"Ollama API URL (default: {OLLAMA_URL})")
args = parser.parse_args()

USE_LOOPBACK    = args.stereo
TRANSLATE_MODEL = args.model
OLLAMA_URL      = args.ollama

if args.list:
    pa = pyaudio.PyAudio()
    print(f"{'Index':<6} {'Name':<55} In")
    print("-" * 70)
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0:
            print(f"{i:<6} {d['name']:<55} {d['maxInputChannels']}")
    pa.terminate()
    sys.exit(0)

# ── Load Whisper model ───────────────────────────────────
print("Loading faster-whisper model...")
try:
    fw_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    print(f"✅ faster-whisper [{WHISPER_MODEL}] ready")
except Exception as e:
    print(f"❌ Failed to load model: {e}")
    sys.exit(1)

vad = webrtcvad.Vad(args.vad)


# ── Translation ──────────────────────────────────────────
def translate_to_japanese(english_text: str) -> str:
    prompt = (
        "Translate the following English text to Japanese. "
        "Output only the Japanese translation, nothing else.\n\n"
        + english_text
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": TRANSLATE_MODEL, "prompt": prompt,
                  "stream": False, "think": False},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        return f"(translation error: {e})"


# ── Display ──────────────────────────────────────────────
def print_result(english: str, japanese: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print()
    print("─" * 60)
    print(f"[{ts}] 🇬🇧  {english}")
    print(f"         🇯🇵  {japanese}")
    print("─" * 60)
    print()


# ── Worker thread (transcribe + translate) ───────────────
audio_queue: queue.Queue[np.ndarray] = queue.Queue()

def worker() -> None:
    while True:
        audio = audio_queue.get()
        if audio is None:
            break
        segments, _ = fw_model.transcribe(
            audio, language="en", beam_size=3, vad_filter=False
        )
        english = " ".join(s.text.strip() for s in segments).strip()
        if not english:
            continue
        print(f"\r  🔍 {english}")
        print("  🌐 Translating...", end="", flush=True)
        japanese = translate_to_japanese(english)
        print_result(english, japanese)

worker_thread = threading.Thread(target=worker, daemon=True)
worker_thread.start()


# ── Resample (e.g. 48000 → 16000) ───────────────────────
def resample(audio: np.ndarray, orig_rate: int) -> np.ndarray:
    if orig_rate == SAMPLE_RATE:
        return audio
    target_len = int(len(audio) * SAMPLE_RATE / orig_rate)
    return np.interp(
        np.linspace(0, len(audio) - 1, target_len),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


# ── Main ─────────────────────────────────────────────────
def main() -> None:
    pa = pyaudio.PyAudio()

    if USE_LOOPBACK:
        loopback  = pa.get_default_wasapi_loopback()
        rate      = int(loopback["defaultSampleRate"])
        channels  = loopback["maxInputChannels"]
        dev_index = loopback["index"]
        dev_name  = loopback["name"]
    else:
        default   = pa.get_default_input_device_info()
        rate      = 16000
        channels  = 1
        dev_index = default["index"]
        dev_name  = default["name"]

    print()
    print("=" * 60)
    print("  🌐 Real-time Voice Translator  (English → Japanese)")
    print("=" * 60)
    print(f"  🎤 Input   : {dev_name}")
    print(f"  🔊 Rate    : {rate} Hz  |  VAD aggressiveness: {args.vad}")
    print(f"  ✂️  Split at: {SILENCE_THRESHOLD}s silence  |  Max buffer: {args.max}s")
    print(f"  🤖 Model   : {TRANSLATE_MODEL}")
    print("  Ctrl+C to quit")
    print("=" * 60)
    print()
    print("  👂 Listening...")

    native_frame      = int(rate * FRAME_DURATION / 1000)
    silence_frames    = int(SILENCE_THRESHOLD * 1000 / FRAME_DURATION)
    min_speech_frames = int(MIN_SPEECH_SECS * 1000 / FRAME_DURATION)
    max_speech_frames = int(args.max * 1000 / FRAME_DURATION)

    speech_buffer: list[np.ndarray] = []
    silent_count  = 0
    in_speech     = False

    def callback(in_data, frame_count, time_info, status):
        nonlocal silent_count, in_speech, speech_buffer

        chunk = np.frombuffer(in_data, dtype=np.float32)

        # Stereo → mono
        if channels > 1:
            chunk = chunk.reshape(-1, channels).mean(axis=1)

        chunk_16k  = resample(chunk, rate)
        chunk_int16 = (chunk_16k * 32768).clip(-32768, 32767).astype(np.int16)

        # Skip if frame size doesn't match VAD expectation
        if len(chunk_int16.tobytes()) != FRAME_SIZE * 2:
            return (None, pyaudio.paContinue)

        try:
            is_speech = vad.is_speech(chunk_int16.tobytes(), SAMPLE_RATE)
        except Exception:
            is_speech = False

        if is_speech:
            speech_buffer.append(chunk_16k)
            silent_count = 0
            if not in_speech:
                in_speech = True
                print("\r  🎙️  Detecting...", end="", flush=True)
            elif len(speech_buffer) >= max_speech_frames:
                # Force flush for long continuous speech
                audio_queue.put(np.concatenate(speech_buffer))
                speech_buffer = []
                silent_count  = 0
                print("\r  🔄 Continuing...", end="", flush=True)
        else:
            if in_speech:
                speech_buffer.append(chunk_16k)
                silent_count += 1
                if silent_count >= silence_frames:
                    in_speech = False
                    if len(speech_buffer) >= min_speech_frames:
                        audio_queue.put(np.concatenate(speech_buffer))
                    speech_buffer = []
                    silent_count  = 0
                    print("\r  👂 Listening...         ", end="", flush=True)

        return (None, pyaudio.paContinue)

    stream = pa.open(
        format=pyaudio.paFloat32,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=dev_index,
        frames_per_buffer=native_frame,
        stream_callback=callback,
    )
    stream.start_stream()

    try:
        while stream.is_active():
            import time
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\n👋 Stopped.")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        audio_queue.put(None)
        worker_thread.join(timeout=3)


if __name__ == "__main__":
    main()
