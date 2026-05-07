# live-loopback-translator

Transcribes and translates English speech to Japanese in real-time.

Built because I needed to understand English-only video calls and YouTube content — no cloud API, no subscriptions, just local models.

## How it works

1. Captures audio via microphone or system loopback (WASAPI)
2. [webrtcvad](https://github.com/wiseman/py-webrtcvad) detects speech start/end in 30ms chunks
3. [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes each utterance
4. [Ollama](https://ollama.com) (local LLM) translates to Japanese
5. Result printed to terminal with timestamp

No audio is sent to any external service.

## Requirements

- **Windows** (uses WASAPI loopback for system audio capture)
- **Python 3.10+**
- **[Ollama](https://ollama.com)** running locally on port 11434
  - Pull a model: `ollama pull qwen3.5:9b` (or any model you prefer)

## Installation

```bash
git clone https://github.com/pepepepepepo/live-loopback-translator
cd live-loopback-translator

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```bash
# Translate your microphone input
python voice_translator.py

# Translate system audio (YouTube, video calls, etc.)
python voice_translator.py --stereo

# List available audio devices
python voice_translator.py --list
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--stereo` | off | Use WASAPI loopback (system audio) |
| `--vad 0-3` | `2` | VAD sensitivity. 0=lenient, 3=strict |
| `--max N` | `7.0` | Force translation every N seconds (for long continuous speech) |
| `--model NAME` | `qwen3.5:9b` | Ollama model to use for translation |

### Examples

```bash
# YouTube with forced translation every 5 seconds
python voice_translator.py --stereo --max 5

# Quiet environment, stricter VAD to reduce noise
python voice_translator.py --stereo --vad 3

# Use a different translation model
python voice_translator.py --stereo --model gemma3:12b
```

## Notes

- **Whisper model**: Uses `small.en` by default (~461MB, downloads automatically on first run). Faster but English-only.
- **Translation speed**: Depends on your CPU and Ollama model. `qwen3.5:9b` on a mid-range CPU takes ~1-2 seconds per utterance.
- **VAD tuning**: If it picks up too much background noise, increase `--vad` to 3. If it cuts off speech too early, try `--vad 1`.

## License

MIT
