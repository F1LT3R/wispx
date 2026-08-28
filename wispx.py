"""wispx — local push-to-talk dictation.

Hold Ctrl+Option, speak, release → text is pasted at the cursor (Cmd+V).

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
"""
import os
import sys
import threading
import time

import numpy as np
import pyaudio
import pyperclip
from faster_whisper import WhisperModel
from pynput import keyboard

MODEL_SIZE = sys.argv[1] if len(sys.argv) > 1 else "base"
LANGUAGE = "en"

print(f"Loading faster-whisper model '{MODEL_SIZE}' (int8, cpu)...", flush=True)
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
print("Model ready.", flush=True)


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
        print(f"Mic: '{self.name}' input(s) {[c+1 for c in self.chans]} -> mono",
              flush=True)

    def start_recording(self):
        self.is_recording = True
        self.audio_data = []
        thread = threading.Thread(target=self._record)
        thread.daemon = True
        thread.start()

    def _record(self):
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
            while self.is_recording:
                data = self.stream.read(1024, exception_on_overflow=False)
                buf = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                buf = buf.reshape(-1, self.dev_channels)
                chunk = buf[:, self.chans].mean(axis=1) / 32768.0
                self.audio_data.append(chunk)
        except Exception as e:
            print(f"Recording error: {e}")
        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None

    def stop_recording(self):
        self.is_recording = False
        time.sleep(0.2)


recorder = AudioRecorder()
keys_pressed = set()


def on_press(key):
    try:
        keys_pressed.add(key)
        if (keyboard.Key.ctrl_l in keys_pressed or keyboard.Key.ctrl_r in keys_pressed) and \
           (keyboard.Key.alt_l in keys_pressed or keyboard.Key.alt_r in keys_pressed):
            if not recorder.is_recording:
                print("Recording started...", flush=True)
                recorder.start_recording()
    except AttributeError:
        pass


def on_release(key):
    try:
        keys_pressed.discard(key)
        if recorder.is_recording:
            print("Recording stopped, transcribing...", flush=True)
            recorder.stop_recording()
            if recorder.audio_data:
                transcribe_and_output(recorder.audio_data)
    except AttributeError:
        pass


def transcribe_and_output(audio_data):
    """Transcribe audio and paste at the cursor."""
    if not audio_data:
        return
    audio = np.concatenate(audio_data)

    # Skip near-silence so we don't paste garbage on an accidental tap.
    rms = float(np.sqrt((audio**2).mean()))
    if rms < 0.003:
        print(f"(silence, skipped — rms {rms:.4f}. If you were speaking, "
              f"check MIC_IN / run mic_scan.py)", flush=True)
        return

    t0 = time.time()
    try:
        segments, _info = model.transcribe(
            audio,
            language=LANGUAGE,
            beam_size=1,        # greedy = fastest
            vad_filter=True,    # trim silence before/after speech
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as e:
        print(f"Transcription error: {e}", flush=True)
        return

    if not text:
        print("(no speech detected)", flush=True)
        return

    dt = time.time() - t0
    dur = len(audio) / 16000
    print(f"Transcribed in {dt:.2f}s ({dur:.1f}s audio, rms {rms:.4f}): {text}", flush=True)

    pyperclip.copy(text)
    time.sleep(0.2)
    from pynput.keyboard import Controller, Key
    kb = Controller()
    kb.press(Key.cmd)
    kb.press("v")
    kb.release("v")
    kb.release(Key.cmd)


def listen_for_hotkey():
    print("Listening for Ctrl+Option... (press and hold to record, release to stop)", flush=True)
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    listen_for_hotkey()
