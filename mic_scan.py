"""mic_scan.py — find which device/channel your microphone is actually on.

Usage:
    python3 mic_scan.py                # system default device
    python3 mic_scan.py 13             # specific device index
    python3 mic_scan.py --all          # every input device (3s each)

It waits for you to press ENTER, captures for 8 seconds, and prints live
per-channel levels. SPEAK DURING THE CAPTURE. Speech shows rms > 0.01.

Use the result with wispx:
    MIC_IN=3,4 python3 wispx.py        # 1-based channel numbers
    MIC_DEVICE=13 python3 wispx.py     # device index from the list
"""
import sys
import time

import numpy as np
import pyaudio


def live_scan(p, idx, seconds=8.0, no_prompt=False):
    info = p.get_device_info_by_index(idx)
    ch = min(int(info["maxInputChannels"]), 16)
    rate = int(info["defaultSampleRate"])
    print(f"\n=== Device #{idx}: {info['name']} — {ch} inputs @ {rate}Hz")
    if not no_prompt:
        input(f"    Press ENTER when ready, then SPEAK during the next "
              f"{seconds:.0f} seconds...")

    s = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
               input_device_index=idx, frames_per_buffer=rate // 10)  # 100ms

    col_width = 8
    print("    " + " ".join(f"{k+1:>{col_width}}" for k in range(ch)))
    peak = np.zeros(ch)
    t0 = time.time()
    row = 0
    while time.time() - t0 < seconds:
        data = s.read(rate // 10, exception_on_overflow=False)
        buf = np.frombuffer(data, dtype=np.int16).astype(
            np.float32).reshape(-1, ch) / 32768.0
        rms = np.sqrt((buf**2).mean(axis=0))
        peak = np.maximum(peak, rms)
        row += 1
        if row % 5 == 0:  # print every 0.5s
            vals = [f"{v:>{col_width}.4f}" for v in rms]
            print(f"    {time.time()-t0:4.1f}s " + " ".join(vals))
    s.close()

    active = [str(k + 1) for k, v in enumerate(peak) if v > 0.005]
    print(f"    >>> peak per channel: "
          + ", ".join(f"ch{k+1}={v:.4f}" for k, v in enumerate(peak)
                      if v > 0.0005) or "all silent")
    print(f"    >>> ACTIVE channels (1-based): {', '.join(active) or 'NONE'}")


def main():
    argv = sys.argv[1:]
    scan_all = "--all" in argv
    args = [a for a in argv if a != "--all"]

    p = pyaudio.PyAudio()
    input_devs = [i for i in range(p.get_device_count())
                  if p.get_device_info_by_index(i)["maxInputChannels"] > 0]

    if scan_all:
        per = float(args[0]) if args else 3.0
        print(f"Scanning {len(input_devs)} devices, {per:.0f}s each "
              f"(~{len(input_devs)*per:.0f}s total).")
        input("Press ENTER when ready, then SPEAK LOUDLY and KEEP TALKING "
              f"for the whole {len(input_devs)*per:.0f}s...")
        for i in input_devs:
            live_scan(p, i, per, no_prompt=True)
        print("\nSpeech = rms > 0.01. Use that device (MIC_DEVICE) and "
              "channels (MIC_IN) with wispx.")
    else:
        idx = int(args[0]) if args else p.get_default_input_device_info()["index"]
        live_scan(p, idx)


if __name__ == "__main__":
    main()
