"""wispx — local push-to-talk dictation.

Hold Ctrl+Option, speak, release → the transcribed text is copied to
the clipboard via pbcopy and pasted at the cursor (Cmd+V).

Uses faster-whisper (CTranslate2, int8) instead of openai-whisper:
  - no "FP16 is not supported on CPU" warnings
  - ~4-8x faster on CPU (incl. Apple Silicon)

Model size:  python3 wispx.py          # base (default, fastest of the useful ones)
             python3 wispx.py small    # slightly slower, more accurate
             python3 wispx.py medium   # slower, most accurate

Mic input (defaults: system default device, analog inputs 3+4 of the
Clarett+ 8Pre mixed to mono):
    MIC_DEVICE=13 python3 wispx.py        # device index (omit = system default)
    MIC_IN=5,6 python3 wispx.py           # 1-based input channels to use
    python3 mic_scan.py                   # list devices + per-channel levels

Tech vocabulary (bias the decoder toward terms + fix known misses):
    WISPX_TERMS=/path/terms.txt WISPX_ALIASES=/path/aliases.txt python3 wispx.py
    (defaults: terms.txt / aliases.txt next to wispx.py)
    The prompt uses the first PROMPT_MAX_TERMS of terms.txt (order =
    priority) and is skipped on takes shorter than PROMPT_MIN_SECONDS
    (~a lone word), where a prompt tends to blank or echo the list;
    those takes rely on the aliases.txt post-pass.
"""
import os
import re
import subprocess
import sys
import threading
import time

import numpy as np
import pyaudio
from faster_whisper import WhisperModel
from pynput import keyboard

MODEL_SIZE = sys.argv[1] if len(sys.argv) > 1 else "base"
LANGUAGE = "en"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TERMS_FILE = os.environ.get("WISPX_TERMS") or os.path.join(SCRIPT_DIR, "terms.txt")
ALIASES_FILE = os.environ.get("WISPX_ALIASES") or os.path.join(SCRIPT_DIR, "aliases.txt")

# ANSI styles for terminal output
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DARK_GREY = "\033[90m"           # bright black renders as dark grey
SPOKEN_TEXT = "\033[45;1;37m"    # dark magenta bg, white bold text
LISTENING = "\033[42;1;30m"      # green bg, black bold text
RESET = "\033[0m"

MAX_RECORD_SECONDS = 60.0   # hard cap per take
BAR_WIDTH = 10              # progress-bar cells
BAR_INTERVAL = 0.1          # seconds between bar redraws

# Vocab-prompt tuning. A long initial_prompt destabilizes the decoder on
# very short takes: it either emits nothing or "echoes" the prompt list
# instead of the audio. So the prompt is (a) kept concise — at most the
# first PROMPT_MAX_TERMS of terms.txt (file order = priority) — and
# (b) skipped entirely on takes shorter than PROMPT_MIN_SECONDS (~a lone
# spoken word), which lean on the aliases.txt post-pass instead.
PROMPT_MAX_TERMS = 20
PROMPT_MIN_SECONDS = 1.0

# ---- pure helpers (no side effects; tested by tmp/test_terms.py) ----

def load_lines(path):
    """Stripped non-empty non-comment lines of a text file (missing -> [])."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
    except FileNotFoundError:
        return []
    return [ln for ln in lines if ln and not ln.startswith("#")]


def build_terms_prompt(path, max_terms):
    """Concise initial_prompt from the FIRST max_terms of terms.txt, or None.

    Kept deliberately short: faster-whisper caps initial_prompt at 448
    tokens, and long lists make the decoder unstable (it emits nothing or
    repeats the list instead of the audio). File order = priority, so put
    your most-used terms at the top; only the first max_terms are used.
    """
    terms = load_lines(path)
    if not terms:
        return None
    if len(terms) > max_terms:
        print(f"{YELLOW}📝 Note: {len(terms)} terms in {path}; the prompt uses "
              f"the first {max_terms} (order = priority).{RESET}", flush=True)
    return f"Dictation of terminal and tech terms: {', '.join(terms[:max_terms])}."


def load_aliases(path):
    """Parse 'from -> to' lines into [(source, target, compiled_regex), ...].

    Matching is case-insensitive on word boundaries, so 'Alice' hits
    'alice'/'Alice.' but not 'allice'. Malformed lines are skipped with
    a note (never fatal).
    """
    patterns = []
    for line in load_lines(path):
        if " -> " not in line:
            print(f"{YELLOW}⚠️ Skipping malformed alias line (want 'from -> to'): "
                  f"{line!r}{RESET}", flush=True)
            continue
        src, dst = (part.strip() for part in line.split(" -> ", 1))
        if not src or not dst:
            print(f"{YELLOW}⚠️ Skipping malformed alias line: {line!r}{RESET}",
                  flush=True)
            continue
        rx = re.compile(r"\b" + re.escape(src) + r"\b", re.IGNORECASE)
        patterns.append((src, dst, rx))
    return patterns


def apply_aliases(text, patterns):
    """Apply alias patterns to text. Returns (new_text, [(source, target)])."""
    applied = []
    for src, dst, rx in patterns:
        text, n = rx.subn(dst, text)
        if n:
            applied.append((src, dst))
    return text, applied


def should_use_prompt(dur, min_seconds):
    """True when a take is long enough that the vocab prompt helps rather
    than hurts. Takes shorter than min_seconds (~a lone word) skip it and
    rely on the alias post-pass — a prompt on a short take tends to come
    back empty or echo the list instead of the audio."""
    return dur >= min_seconds

# ---- end pure helpers ----

print(f"⏳ Loading faster-whisper model '{MODEL_SIZE}' (int8, cpu)...", flush=True)
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
print("✅ Model ready.", flush=True)

TERMS_PROMPT = build_terms_prompt(TERMS_FILE, PROMPT_MAX_TERMS)
ALIAS_PATTERNS = load_aliases(ALIASES_FILE)
n_terms = len(load_lines(TERMS_FILE))
print(f"📚 Vocabulary: {n_terms} terms in {TERMS_FILE} "
      f"(prompt: first {min(n_terms, PROMPT_MAX_TERMS)}), "
      f"{len(ALIAS_PATTERNS)} aliases in {ALIASES_FILE}.", flush=True)


def mic_config():
    """(device_index_or_None, [0-based channel indexes]) from env vars."""
    device = int(os.environ["MIC_DEVICE"]) if os.environ.get("MIC_DEVICE") else None
    chans = [int(c) - 1 for c in os.environ.get("MIC_IN", "3,4").split(",")]
    return device, chans


class AudioRecorder:
    def __init__(self):
        self.is_recording = False
        self.audio_data = []
        self.stream = None
        self._finish_lock = threading.Lock()
        self.finished = False
        self.record_start = 0.0
        self._thread = None
        self.p = pyaudio.PyAudio()
        self.device, self.chans = mic_config()
        info = (self.p.get_device_info_by_index(self.device) if self.device is not None
                else self.p.get_default_input_device_info())
        self.name = info["name"]
        self.dev_channels = int(info["maxInputChannels"])
        if max(self.chans) >= self.dev_channels:
            raise SystemExit(
                f"MIC_IN channels {self.chans} out of range for '{self.name}' "
                f"({self.dev_channels} input channels)")
        print(f"{RED}🎙️ Mic: '{self.name}' input(s) {[c+1 for c in self.chans]} "
              f"-> mono{RESET}", flush=True)

    def start_recording(self):
        self.is_recording = True
        self.audio_data = []
        self.finished = False
        self.record_start = time.time()
        thread = threading.Thread(target=self._record)
        thread.daemon = True
        self._thread = thread
        thread.start()
        self._draw_bar(0.0)

    def _record(self):
        timed_out = False
        try:
            # Open the device at its full channel count, then select the
            # mic channel(s) ourselves — PortAudio can't address channels.
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=self.dev_channels,
                rate=16000,
                input=True,
                input_device_index=self.device,
                frames_per_buffer=1024,
            )
            next_draw = time.time()
            while self.is_recording:
                data = self.stream.read(1024, exception_on_overflow=False)
                buf = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                buf = buf.reshape(-1, self.dev_channels)
                chunk = buf[:, self.chans].mean(axis=1) / 32768.0
                self.audio_data.append(chunk)
                now = time.time()
                elapsed = now - self.record_start
                if elapsed >= MAX_RECORD_SECONDS:
                    self.is_recording = False
                    timed_out = True
                    break
                if now >= next_draw:
                    self._draw_bar(elapsed)
                    next_draw = now + BAR_INTERVAL
        except Exception as e:
            print(f"❌ {RED}Recording error: {e}{RESET}", flush=True)
        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
        if timed_out:
            self.finish_recording("timeout")

    def _draw_bar(self, elapsed):
        frac = min(elapsed / MAX_RECORD_SECONDS, 1.0)
        filled = int(frac * BAR_WIDTH)
        bar = "#" * filled + "-" * (BAR_WIDTH - filled)
        print(f"\r{DARK_GREY}[{bar}] {elapsed:4.1f}s / {MAX_RECORD_SECONDS:.0f}s{RESET}",
              end="", flush=True)

    def finish_recording(self, reason):
        """Idempotent take-finisher. reason: 'release' or 'timeout'.
        Safe to call from the main thread and the record thread; the lock
        guarantees the take is finished exactly once."""
        with self._finish_lock:
            if self.finished:
                return
            self.finished = True
            self.is_recording = False
        if self._thread and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)   # winner on main thread waits for the reader
        elapsed = min(time.time() - self.record_start, MAX_RECORD_SECONDS)
        self._draw_bar(elapsed)              # finalize the bar in place
        print()                              # terminate the bar line
        if reason == "timeout":
            print(f"⏰ {YELLOW}Max recording time ({MAX_RECORD_SECONDS:.0f}s) reached, "
                  f"transcribing...{RESET}", flush=True)
        else:
            print(f"⏹️ {CYAN}Recording stopped, transcribing...{RESET}", flush=True)
        if self.audio_data:
            transcribe_and_output(self.audio_data)
        else:
            # A very brief tap can be released before the record thread has
            # finished opening the mic, so no buffer was ever read.
            print(f"{YELLOW}⚠️ No audio captured — the take was too short to "
                  f"record anything. Hold the hotkey a little longer.{RESET}",
                  flush=True)


recorder = AudioRecorder()
keys_pressed = set()


def on_press(key):
    try:
        keys_pressed.add(key)
        if keyboard.Key.ctrl_l in keys_pressed and keyboard.Key.alt_l in keys_pressed:
            if not recorder.is_recording:
                print(f"🔴 {YELLOW}Recording started...{RESET}", flush=True)
                recorder.start_recording()
    except AttributeError:
        pass


def on_release(key):
    try:
        keys_pressed.discard(key)
        if recorder.is_recording:
            recorder.finish_recording("release")
    except AttributeError:
        pass


def print_block(text, style):
    """Print text as a colored block padded 1 char left and right, with a
    blank line before and after. Multi-line text is formatted line by line."""
    print(flush=True)
    for line in text.split("\n"):
        print(f"{style} {line} {RESET}", flush=True)
    print(flush=True)


def transcribe_and_output(audio_data):
    """Transcribe audio and paste at the cursor."""
    if not audio_data:
        return
    audio = np.concatenate(audio_data)

    # Skip near-silence so we don't paste garbage on an accidental tap.
    rms = float(np.sqrt((audio**2).mean()))
    if rms < 0.003:
        print(f"🤫 Silence — nothing to transcribe "
              f"({DARK_GREY}rms {rms:.4f}{RESET}). If you were speaking, "
              f"check MIC_IN or run mic_scan.py.", flush=True)
        return

    t0 = time.time()
    dur = len(audio) / 16000
    # The vocab prompt is for phrases. On a short take (~a lone word) it is
    # more likely to come back empty or echo the prompt list than to help,
    # so short takes skip it and lean on the alias post-pass below.
    use_prompt = TERMS_PROMPT is not None and \
        should_use_prompt(dur, PROMPT_MIN_SECONDS)
    if TERMS_PROMPT is not None and not use_prompt:
        print(f"✂️ {DARK_GREY}short take ({dur:.1f}s < {PROMPT_MIN_SECONDS:.1f}s): "
              f"vocab prompt off, alias pass still applies{RESET}", flush=True)
    try:
        segments, _info = model.transcribe(
            audio,
            language=LANGUAGE,
            beam_size=1,        # greedy = fastest
            vad_filter=True,    # trim silence before/after speech
            initial_prompt=TERMS_PROMPT if use_prompt else None,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        # Whisper's decoder ends segments with a terminal period even when
        # the utterance is incomplete — drop it so fragments don't land
        # with one.
        text = text.removesuffix(".").rstrip()
        # Fix known mis-transcriptions (terms.txt makes them rarer,
        # aliases.txt makes the survivors land correctly).
        text, applied = apply_aliases(text, ALIAS_PATTERNS)
        for src, dst in applied:
            print(f"🪄 {DARK_GREY}alias: {src} → {dst}{RESET}", flush=True)
    except Exception as e:
        print(f"❌ {RED}Transcription error: {e}{RESET}", flush=True)
        return

    if not text:
        print("🤐 No speech detected", flush=True)
        return

    dt = time.time() - t0
    print(f"⚡ {DARK_GREY}Transcribed in {dt:.2f}s ({dur:.1f}s audio, "
          f"rms {rms:.4f}){RESET}", flush=True)
    print_block(text, SPOKEN_TEXT)

    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
    time.sleep(0.2)
    from pynput.keyboard import Controller, Key
    kb = Controller()
    kb.press(Key.cmd)
    kb.press("v")
    kb.release("v")
    kb.release(Key.cmd)


def listen_for_hotkey():
    print_block("👂 Listening for Ctrl+Option (left modifiers only)...\n"
                "(press and hold to record, release to stop — max 60s)", LISTENING)
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    listen_for_hotkey()
