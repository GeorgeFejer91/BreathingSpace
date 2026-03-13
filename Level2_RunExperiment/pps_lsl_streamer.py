#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PPS Experiment LSL Auto-Streamer
( mouse clicks + Komplete-audio → LSL )

Requires: pylsl, sounddevice, numpy
"""

import os, sys, json, time, glob, datetime as dt, threading, re
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import ctypes, numpy as np
from ctypes import wintypes, byref

# ──────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────
RESULTS_DIR        = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\Results"
AUDIO_SAMPLE_RATE  = 44100      # Hz  (fallback to device default if invalid)
AUDIO_CHANNELS     = 2          # stereo
AUDIO_DURATION     = 15 * 60    # seconds (automatic stop)
AUDIO_CHUNK_SIZE   = 1024       # frames / block
AUDIO_DEVICE_NAME  = "Input 3/4"

POLL_INTERVAL      = 0.005      # mouse-polling interval (s)

# ──────────────────────────────────────────────────────────────────────────────
try:
    from pylsl import StreamInfo, StreamOutlet
    import sounddevice as sd
except ImportError as e:
    print("Missing libraries – install with:\n  pip install pylsl sounddevice numpy")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
class PPSLSLAutoStreamer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PPS Experiment LSL Auto-Streamer")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # try to get 1 ms timers on Windows
        try:  ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:  pass

        # state
        self.streaming = False
        self.mouse_stream = self.audio_stream = None
        self.mouse_thread = self.audio_thread = None
        self.audio_running = False
        self.audio_chunks_sent = 0
        self.audio_device_id = None
        self.participant_info = None
        self.start_time = None
        self.click_count = 0
        self.last_left = self.last_right = self.last_middle = False

        # GUI
        self._build_ui()
        self.find_participant_info()
        self.find_audio_device()

        # auto-start in 1 s
        self.root.after(1000, self.start_streaming)

    # ───────────────────────── GUI ─────────────────────────
    def _build_ui(self):
        fn_b = ("Arial", 10, "bold")
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="PPS Experiment LSL Auto-Streamer",
                  font=("Arial", 16, "bold")).pack(pady=8)

        # participant
        pbox = ttk.LabelFrame(main, text="Participant", padding=10)
        pbox.pack(fill=tk.X, padx=5, pady=4)
        self.pid_var = tk.StringVar(master=self.root, value="None")
        self.ts_var  = tk.StringVar(master=self.root, value="N/A")
        ttk.Label(pbox, text="ID:").grid(row=0, column=0, sticky="w")
        ttk.Label(pbox, textvariable=self.pid_var, font=fn_b)\
            .grid(row=0, column=1, sticky="w")
        ttk.Label(pbox, text="Timestamp:").grid(row=1, column=0, sticky="w")
        ttk.Label(pbox, textvariable=self.ts_var)\
            .grid(row=1, column=1, sticky="w")
        ttk.Button(pbox, text="Refresh", command=self.find_participant_info)\
            .grid(row=2, column=0, columnspan=2, pady=6)

        # streams
        sbox = ttk.LabelFrame(main, text="Streams", padding=10)
        sbox.pack(fill=tk.X, padx=5, pady=4)
        self.status_var  = tk.StringVar(master=self.root, value="Initializing…")
        self.mstream_var = tk.StringVar(master=self.root, value="Not active")
        self.astream_var = tk.StringVar(master=self.root, value="Not active")
        self.adev_var    = tk.StringVar(master=self.root, value="Not selected")
        lab = lambda r,c,t: ttk.Label(sbox, text=t).grid(row=r, column=c, sticky="w")
        lab(0,0,"Status:"); ttk.Label(sbox, textvariable=self.status_var, font=fn_b)\
            .grid(row=0,column=1,sticky="w")
        lab(1,0,"Mouse stream:"); ttk.Label(sbox, textvariable=self.mstream_var)\
            .grid(row=1,column=1,sticky="w")
        lab(2,0,"Audio stream:"); ttk.Label(sbox, textvariable=self.astream_var)\
            .grid(row=2,column=1,sticky="w")
        lab(3,0,"Audio device:"); ttk.Label(sbox, textvariable=self.adev_var)\
            .grid(row=3,column=1,sticky="w")

        # clicks
        cbox = ttk.LabelFrame(main, text="Mouse clicks", padding=10)
        cbox.pack(fill=tk.X, padx=5, pady=4)
        self.ccount_var = tk.StringVar(master=self.root, value="0 recorded")
        self.lastclick_var = tk.StringVar(master=self.root, value="Last: –")
        ttk.Label(cbox, textvariable=self.ccount_var, font=("Arial",12,"bold")).pack()
        ttk.Label(cbox, textvariable=self.lastclick_var).pack()
        ttk.Button(cbox, text="STOP STREAMING", command=self.stop_streaming, width=20)\
            .pack(pady=4)

        # log
        lbox = ttk.LabelFrame(main, text="Log", padding=10)
        lbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=4)
        self.log_text = scrolledtext.ScrolledText(lbox, height=10, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, msg):
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        print(line)
        if not self.root.winfo_exists(): return
        def _append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _append)

    # ───────────────────────── participant info ─────────────────────────
    def find_participant_info(self):
        try:
            if not os.path.isdir(RESULTS_DIR):
                self.log(f"Results dir not found → {RESULTS_DIR}")
                return
            files = glob.glob(os.path.join(RESULTS_DIR, "participant_*.*"))
            for d in [p for p in os.listdir(RESULTS_DIR)
                      if p.startswith("participant_") and
                         os.path.isdir(os.path.join(RESULTS_DIR,p))]:
                files.extend(glob.glob(os.path.join(RESULTS_DIR, d, "*.*")))
            if not files:
                self.log("No participant files found")
                return
            f = max(files, key=os.path.getctime)
            m  = re.search(r"participant_(\d+)", os.path.basename(f))
            pid = f"P{m.group(1)}" if m else "Unknown"
            self.participant_info = {
                "participant_id": pid,
                "timestamp": dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            }
            self.pid_var.set(pid); self.ts_var.set(self.participant_info["timestamp"])
            self.log(f"Selected participant {pid}")
        except Exception as e:
            self.log(f"find_participant_info error: {e}")

    # ───────────────────────── audio device search ──────────────────────
    def find_audio_device(self):
        try:
            devs = sd.query_devices()
            matches = [(i,d) for i,d in enumerate(devs)
                       if AUDIO_DEVICE_NAME in d["name"] and
                          d["max_input_channels"]>=AUDIO_CHANNELS]
            if not matches:
                self.adev_var.set("Not found")
                self.log(f"No input containing '{AUDIO_DEVICE_NAME}'")
                return
            self.audio_device_id, info = matches[0]
            self.adev_var.set(f"{self.audio_device_id}: {info['name']}")
            self.log(f"Audio device → {info['name']} (ID {self.audio_device_id})")
        except Exception as e:
            self.adev_var.set("Error"); self.log(f"find_audio_device error: {e}")

    # ───────────────────────── create LSL streams ───────────────────────
    def create_lsl_streams(self):
        try:
            base = "PPS_"+(self.participant_info["participant_id"]
                           if self.participant_info else "Unknown")
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            m_info = StreamInfo(f"{base}_MouseClicks_{ts}", "MouseEvents", 3, 0,
                                "float32", f"mouse_{ts}")
            self.mouse_stream = StreamOutlet(m_info)
            self.mstream_var.set(m_info.name())
            a_info = StreamInfo(f"{base}_Audio_{ts}", "Audio",
                                AUDIO_CHANNELS, AUDIO_SAMPLE_RATE,
                                "float32", f"audio_{ts}")
            self.audio_stream = StreamOutlet(a_info)
            self.astream_var.set(a_info.name())
            self.log("LSL streams created")
            return True
        except Exception as e:
            self.log(f"create_lsl_streams error: {e}")
            return False

    # ───────────────────────── mouse polling ────────────────────────────
    def _cursor(self):
        pt = wintypes.POINT(); ctypes.windll.user32.GetCursorPos(byref(pt)); return pt.x, pt.y
    def _buttons(self):
        U = ctypes.windll.user32.GetAsyncKeyState
        return (U(0x01)&0x8000!=0, U(0x02)&0x8000!=0, U(0x04)&0x8000!=0)

    def poll_mouse(self):
        try:
            while self.streaming:
                l,r,m = self._buttons()
                if l and not self.last_left  : self._click(0)
                if r and not self.last_right : self._click(1)
                if m and not self.last_middle: self._click(2)
                self.last_left, self.last_right, self.last_middle = l,r,m
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            self.log(f"poll_mouse error: {e}")

    def _click(self, idx):
        x,y = self._cursor(); t = time.perf_counter()-self.start_time
        self.mouse_stream.push_sample([t,float(x),float(y)])
        self.click_count+=1
        self.ccount_var.set(f"{self.click_count} recorded")
        self.lastclick_var.set(f"Last: ({x},{y}) {'lrm'[idx]} @ {t:.3f}s")

    # ───────────────────────── audio capture ────────────────────────────
    def stream_audio(self):
        if not (self.audio_stream and self.streaming and self.audio_device_id is not None): return
        self.audio_running=True; self.audio_chunks_sent=0; dev=self.audio_device_id; sr=AUDIO_SAMPLE_RATE
        try: sd.check_input_settings(device=dev,samplerate=sr,channels=AUDIO_CHANNELS,dtype="float32")
        except Exception as e:
            self.log(f"{sr} Hz not accepted ({e}) → falling back to device default"); sr=None
        def cb(indata, frames, time_info, status):
            if status: self.log(f"Audio status: {status}")
            if not self.audio_running: raise sd.CallbackAbort
            self.audio_stream.push_chunk(indata.tolist()); self.audio_chunks_sent+=1
            if self.audio_chunks_sent%50==0: self.log(f"Audio chunks: {self.audio_chunks_sent}")
        try:
            with sd.InputStream(device=dev,channels=AUDIO_CHANNELS,samplerate=sr,
                                blocksize=AUDIO_CHUNK_SIZE,dtype="float32",callback=cb):
                self.log(f"Audio callback opened (sr={sr or 'device default'})")
                while self.audio_running and self.streaming: sd.sleep(100)
        except Exception as e:
            self.log(f"InputStream error: {e}")
        self.log("Audio thread exited"); self.audio_running=False

    # ───────────────────────── control flow ────────────────────────────
    def start_streaming(self):
        if self.streaming: return
        if not self.create_lsl_streams(): return
        self.start_time=time.perf_counter(); self.status_var.set("Streaming"); self.streaming=True
        self.mouse_thread=threading.Thread(target=self.poll_mouse,daemon=True); self.mouse_thread.start()
        if self.audio_device_id is not None:
            self.audio_thread=threading.Thread(target=self.stream_audio,daemon=True); self.audio_thread.start()
        else: self.log("Audio disabled – no device")
        self.log("Streaming started")

    def stop_streaming(self):
        if not self.streaming: return
        self.streaming=False; self.audio_running=False
        if self.audio_thread and self.audio_thread.is_alive(): self.audio_thread.join(timeout=1)
        self.status_var.set("Stopped")
        dur=time.perf_counter()-self.start_time; self.log(f"Stopped (duration {dur:.1f}s)")

    def on_closing(self):
        if self.streaming and not messagebox.askyesno("Quit","Streaming active – quit anyway?"): return
        self.stop_streaming(); self.root.destroy()

# ────────────────────────── main ──────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    PPSLSLAutoStreamer(root)
    root.mainloop()
