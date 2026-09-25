# 🎙️ wispx

**Local push-to-talk dictation for macOS.** Hold a hotkey, speak, release — the text lands at your cursor. No cloud, no subscription, no network.

```
hold ⌃ + ⌥  →  🗣️ speak  →  release  →  📋 text pasted at cursor
```

## ✨ Why

- ⚡ **Fast** — faster-whisper (CTranslate2, int8) runs ~2–3× real-time on CPU (an M4 Max transcribes 3.5 s of speech in ~1.3 s). No "FP16 is not supported on CPU" warnings, no GPU required.
- 🔒 **Private** — everything stays on your machine.
- 🎛️ **Multi-channel aware** — records from specific inputs of a pro interface (e.g. Clarett+ 8Pre analog 3+4), not just "the system mic."
- 🪶 **Light** — ~400 MB RAM resident with the `base` model, sitting idle waiting for the hotkey.

## 📦 Setup

```bash
python3 -m venv whisper_env
source whisper_env/bin/activate
pip install faster-whisper pynput pyperclip pyaudio numpy
```

> Python 3.14 + Homebrew Python works. `PyAudio` needs the standard macOS system headers (bundled with Xcode Command Line Tools).

## 🚀 Usage

```bash
python3 wispx.py            # base model (default) — fastest
python3 wispx.py small      # slightly slower, more accurate
python3 wispx.py medium     # slower, most accurate
```

On first run the model downloads from Hugging Face (`base` ≈ 145 MB) and caches in `~/.cache/huggingface`.

Then in any text field:

1. Hold **Ctrl + Option** — the **left** modifiers only; right Ctrl/Option are ignored
2. Speak
3. Release — or keep holding: a progress bar fills in place and at the
   **60 s** cap the take is finalized automatically → transcribes (~1–2 s)
   and pastes with **⌘V**

`./wispx` runs in the foreground — full program output in this terminal, `Ctrl-C` quits. Add `--quiet` to start it as a background service instead:

```bash
./wispx            # start in the foreground (no-op if already running)
./wispx --quiet    # start in the background (no-op if already running)
./wispx status     # is it running?
./wispx stop       # stop it (foreground or background)
```

Background (`--quiet`) runs log to `~/.wispx.log` and keep a pidfile at `~/.wispx.pid`.

### 📊 Model cheat-sheet (int8, CPU)

| Model  | Speed            | RAM  | Notes                        |
|--------|------------------|------|------------------------------|
| `base`   | ~2–3× real-time  | ~400 MB | fastest that's still good    |
| `small`  | ~1.5–2× real-time| ~700 MB | sweet spot for accuracy      |
| `medium` | slower           | ~1.5 GB | overkill for dictation       |

## 🎚️ Mic configuration

Defaults: the **system default input device**, inputs **3 and 4** (1-based) mixed to mono — verified for a Clarett+ 8Pre with mics on analog 3/4.

The startup line tells you what it's using:

```
🎙️ Mic: 'Clarett+ 8Pre' input(s) [3, 4] -> mono
```

### ⚙️ Environment variables

| Var            | Default | Meaning                                   |
|----------------|---------|-------------------------------------------|
| `MIC_DEVICE`   | *(system default)* | Input device index (see `mic_scan.py`) |
| `MIC_IN`       | `3,4`   | 1-based input channels, comma-separated, mixed to mono |
| `WISPX_TERMS`  | `<repo>/terms.txt` | Vocabulary file fed to the decoder's `initial_prompt` |
| `WISPX_ALIASES`| `<repo>/aliases.txt` | `from -> to` mis-transcription fixes (post-pass) |

```bash
MIC_IN=5,6 python3 wispx.py                 # different inputs
MIC_DEVICE=13 MIC_IN=1,2 python3 wispx.py   # different device + channels
```

**How channel selection works:** PortAudio can't address individual channels of a multi-channel device, so wispx opens the device at its full channel count and picks the wanted channels itself.

### 🔍 Finding your channels — `mic_scan.py`

```bash
python3 mic_scan.py            # scan the default device (ENTER-gated, 8 s)
python3 mic_scan.py 13         # scan a specific device index
python3 mic_scan.py --all      # every input device, 3 s each
```

It waits for **ENTER**, then prints live per-channel levels every 0.5 s. Speak during the capture; speech shows rms > 0.01. Use the reported 1-based channels as `MIC_IN`.

## 📖 Tech vocabulary

Whisper hears *"el-ess"* and likes to guess prose — `Alice`, `Ellis`, `And last`… Two files fix this (defaults sit next to `wispx.py`; override with `WISPX_TERMS` / `WISPX_ALIASES`):

**`terms.txt`** — one term per line, **file order = priority**. wispx feeds the *first 20* of them into a single compact `initial_prompt` sentence that biases the decoder toward your vocabulary (faster-whisper caps `initial_prompt` at 448 tokens; the 20-term cap keeps it concise — long lists make the decoder emit nothing or *echo the list* instead of your words, especially on short takes). It's probabilistic: it makes the right word *more likely*, not guaranteed.

> **Why the 20-term cap and the short-take skip:** a long `initial_prompt` on a very short take (~a lone spoken word like *"el-ess"*) reliably breaks transcription — the decoder returns empty or spits out the prompt list (`Ls, cd, trr…`). So wispx (a) caps the prompt at the first `PROMPT_MAX_TERMS` (20) terms and (b) **skips the prompt entirely on takes shorter than `PROMPT_MIN_SECONDS` (1.0 s)**, leaning on `aliases.txt` for those. Both are constants at the top of `wispx.py` if you want to tune them.

**`aliases.txt`** — `from -> to`, one per line. Applied to the transcript *after* whisper, case-insensitive on word boundaries — a deterministic net for whatever the prompt misses:

```
Alice -> ls
Ellis -> ls
L.S   -> ls
```

Each hit prints a note (`🪄 alias: Alice → ls`) and the *replaced* text is what lands at your cursor. Restart wispx after editing either file. Caveat: aliases match anywhere in the text (`What is -> ls` will also rewrite "what is the weather"), so only add mappings you'd accept everywhere.

## 🚨 Troubleshooting

| Symptom | Fix |
|---|---|
| **All channels read `0.0000`** in `mic_scan.py` | macOS mic privacy. **System Settings → Privacy & Security → Microphone** — allow your terminal (and Python). This silently returns pure zero, not a low noise floor. |
| `🤫 Silence — nothing to transcribe` (rms 0.0000…) | You weren't on the recorded input. Run `mic_scan.py`, set `MIC_IN`. |
| `🤫 Silence — nothing to transcribe` (rms 0.02…) | You spoke but it was below threshold — unlikely; check input gain on your interface. |
| `⚠️ No audio captured` | Your tap was too brief — the mic takes a few hundred ms to open. Hold the hotkey a little longer. |
| Text transcribes but doesn't paste | **System Settings → Privacy & Security → Accessibility** — allow your terminal (pynput simulates ⌘V). |
| A command name lands as `Alice` / `Ellis` / … | Add the spoken form to `terms.txt` (decoder bias) and the miss to `aliases.txt` (guaranteed fix). |
| "FP16 is not supported on CPU" | You're running plain `openai-whisper`. wispx uses faster-whisper int8 — this warning shouldn't appear. |

## 🗂️ Files

| File | Purpose |
|---|---|
| `wispx.py` | The dictation service: hotkey → record → transcribe → paste |
| `terms.txt` | Vocabulary list → `initial_prompt` decoder bias |
| `aliases.txt` | `from -> to` mis-transcription fixes (post-transcription pass) |
| `wispx` | Shell script: foreground start by default (`--quiet` for a single-instance background service); stop/status |
| `mic_scan.py` | Input-device/channel level scanner |
| `whisper_env/` | Python venv (not tracked) |

## 🧠 Under the hood

- **Recording** — PyAudio at 16 kHz (Core Audio resamples from the device's native rate, e.g. 96 kHz, transparently); each take is capped at 60 s with an in-place progress bar, and the cap finalizes the take exactly like a release; a very brief tap that captures no audio (the mic takes a few hundred ms to open) and a near-silent take (rms < 0.003) are skipped with a note instead of transcribed
- **Transcription** — faster-whisper, greedy decoding (`beam_size=1`) + VAD filter to trim leading/trailing silence; an `initial_prompt` seeded from the first 20 `terms.txt` words biases the decoder toward tech vocabulary — **skipped on takes under 1.0 s**, where a prompt tends to blank or echo the list — then a word-boundary alias pass from `aliases.txt` rewrites known misses; the trailing period Whisper appends even to incomplete sentences is stripped before paste
- **Output** — clipboard + simulated ⌘V, ~0.2 s after copy so the target app has focus
