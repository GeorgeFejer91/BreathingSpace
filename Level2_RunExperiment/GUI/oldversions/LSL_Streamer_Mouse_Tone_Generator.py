#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Combined LSL Streamer and Click Tone Generator
- Streams mouse clicks to LSL
- Plays tones on Output 3/4 when mouse is clicked
- Optionally streams audio from Input 3/4 to LSL
- Automatically retrieves participant ID from metadata files

Requires: pylsl, sounddevice, numpy
"""

import os, sys, time, threading, datetime as dt
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import ctypes, numpy as np
import json, glob
from ctypes import wintypes, byref

# ──────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────
# Metadata Directory
METADATA_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\Results\participant_metadata"

# LSL Configuration
LSL_STREAM_NAME    = "MouseClicks"
LSL_STREAM_TYPE    = "MouseEvents"
AUDIO_SAMPLE_RATE  = 44100      # Hz (fallback to device default if invalid)
AUDIO_CHANNELS     = 2          # stereo
AUDIO_CHUNK_SIZE   = 1024       # frames / block
AUDIO_IN_NAME      = "Input 3/4"  # Audio input device name substring
AUDIO_OUT_NAME     = "Output 3/4" # Audio output device name substring

# Tone Configuration
TONE_FREQUENCY     = 1000       # Hz
TONE_DURATION      = 0.1        # seconds
TONE_AMPLITUDE     = 0.25       # 0.0-1.0

# Mouse Polling
POLL_INTERVAL      = 0.005      # seconds

# ──────────────────────────────────────────────────────────────────────────────
try:
    from pylsl import StreamInfo, StreamOutlet
    import sounddevice as sd
except ImportError as e:
    print("Missing libraries – install with:\n  pip install pylsl sounddevice numpy")
    sys.exit(1)

# Set sounddevice for low latency
sd.default.latency = 'low'

# ──────────────────────────────────────────────────────────────────────────────
def get_participant_info():
    """
    Retrieves the participant ID from the latest JSON file in the metadata directory
    Returns: participant_id (str), full_metadata (dict)
    """
    if not os.path.exists(METADATA_DIR):
        print(f"Metadata directory does not exist: {METADATA_DIR}")
        return "Unknown", {"participant_id": "Unknown", "timestamp": dt.datetime.now().strftime("%Y%m%d_%H%M%S")}
    
    # Find all JSON files in the directory
    json_files = glob.glob(os.path.join(METADATA_DIR, "*.json"))
    
    if not json_files:
        print(f"No JSON files found in {METADATA_DIR}")
        return "Unknown", {"participant_id": "Unknown", "timestamp": dt.datetime.now().strftime("%Y%m%d_%H%M%S")}
    
    # Get the most recent file based on modification time
    latest_file = max(json_files, key=os.path.getmtime)
    
    try:
        # Read and parse the JSON file
        with open(latest_file, 'r') as f:
            metadata = json.load(f)
        
        participant_id = metadata.get("participant_id", "Unknown")
        print(f"Found participant ID: {participant_id} from file: {os.path.basename(latest_file)}")
        return participant_id, metadata
    
    except Exception as e:
        print(f"Error reading metadata file: {e}")
        return "Unknown", {"participant_id": "Unknown", "timestamp": dt.datetime.now().strftime("%Y%m%d_%H%M%S")}

# ──────────────────────────────────────────────────────────────────────────────
class ToneGenerator:
    """Generates and plays tones on mouse clicks using Output 3/4"""
    def __init__(self, frequency=TONE_FREQUENCY, duration=TONE_DURATION, 
                 sample_rate=AUDIO_SAMPLE_RATE, amplitude=TONE_AMPLITUDE):
        self.frequency = frequency
        self.duration = duration
        self.sample_rate = sample_rate
        self.amplitude = amplitude
        self.output_id = None
        self.stream = None
        self.active = False
        
        # Generate the tone once and reuse it
        self.tone_data = self._generate_tone()
        
        # Find output device
        self._find_output_device()
        
        # Open the stream
        self._open_stream()
        
    def _generate_tone(self):
        """Generate the tone signal with fade in/out"""
        t = np.linspace(0, self.duration, int(self.sample_rate * self.duration), endpoint=False)
        tone = self.amplitude * np.sin(2 * np.pi * self.frequency * t).astype(np.float32)
        
        # Apply fade in/out to prevent clicks
        fade_samples = int(0.01 * self.sample_rate)  # 10ms fade
        fade_in = np.linspace(0, 1, fade_samples)
        fade_out = np.linspace(1, 0, fade_samples)
        
        tone[:fade_samples] *= fade_in
        tone[-fade_samples:] *= fade_out
        
        # Create stereo version
        stereo_tone = np.column_stack((tone, tone))
        return stereo_tone
    
    def _find_output_device(self):
        """Find the Output 3/4 device"""
        try:
            devices = sd.query_devices()
            
            # Default to the default output device
            self.output_id = sd.default.device[1]
            
            # Look for Output 3/4
            for i, device in enumerate(devices):
                if device['max_output_channels'] > 0:
                    name = device['name'].lower()
                    if AUDIO_OUT_NAME.lower() in name:
                        self.output_id = i
                        print(f"Found output device: ID {i} - {device['name']}")
                        return
            
            print(f"Output 3/4 not found. Using default output device ID {self.output_id}")
        except Exception as e:
            print(f"Error finding output device: {e}")
    
    def _open_stream(self):
        """Open a persistent audio stream"""
        try:
            # Create the stream with a callback
            self.stream = sd.OutputStream(
                device=self.output_id,
                channels=2,
                callback=self._stream_callback,
                samplerate=self.sample_rate,
                blocksize=256,  # Small blocksize for low latency
                dtype='float32'
            )
            
            # Start the stream
            self.stream.start()
            print(f"Tone output stream opened on device {self.output_id}")
            
            # Position in the tone data
            self.position = 0
            
        except Exception as e:
            print(f"Error opening output stream: {e}")
            self.stream = None
    
    def _stream_callback(self, outdata, frames, time, status):
        """Audio stream callback"""
        if status:
            print(f"Output stream status: {status}")
            
        if not self.active:
            # If not active, output silence
            outdata.fill(0)
            return
            
        # Playing the tone
        if self.position + frames > len(self.tone_data):
            # End of tone
            remaining = len(self.tone_data) - self.position
            if remaining > 0:
                outdata[:remaining] = self.tone_data[self.position:self.position+remaining]
            outdata[remaining:] = 0
            self.position = 0
            self.active = False
        else:
            # Continue playing
            outdata[:] = self.tone_data[self.position:self.position+frames]
            self.position += frames
    
    def play_tone(self):
        """Play the tone"""
        if self.stream is None or not self.stream.active:
            # Try to reopen if needed
            self._open_stream()
            if self.stream is None:
                print("Cannot play tone: no active audio stream")
                return
        
        # Reset position and set active flag
        self.position = 0
        self.active = True
    
    def cleanup(self):
        """Clean up resources"""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

# ──────────────────────────────────────────────────────────────────────────────
class CombinedStreamer:
    """Combined LSL Streamer and Click Tone Generator"""
    def __init__(self, root):
        self.root = root
        self.root.title("Mouse Click LSL Streamer + Tone Generator")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Try to get 1ms timers on Windows
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

        # Retrieve participant information
        self.participant_id, self.participant_metadata = get_participant_info()

        # State
        self.streaming = False
        self.mouse_stream = None
        self.audio_stream = None
        self.mouse_thread = None
        self.audio_thread = None
        self.audio_running = False
        self.audio_device_id = None
        self.start_time = None
        self.click_count = 0
        self.last_left = self.last_right = self.last_middle = False
        
        # Tone generator
        self.tone_generator = ToneGenerator()
        
        # Tone settings
        self.tone_frequency = TONE_FREQUENCY
        self.tone_amplitude = TONE_AMPLITUDE
        
        # Audio input enabled flag
        self.audio_input_enabled = True

        # Build the UI
        self._build_ui()
        
        # Find audio input device
        self.find_audio_device()
        
        # Auto-start in 1 second
        self.root.after(1000, self.start_streaming)

    # ───────────────────────── GUI ─────────────────────────
    def _build_ui(self):
        fn_b = ("Arial", 10, "bold")
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="Mouse Click LSL Streamer + Tone Generator",
                  font=("Arial", 16, "bold")).pack(pady=8)

        # Participant info
        pbox = ttk.LabelFrame(main, text="Participant Information", padding=10)
        pbox.pack(fill=tk.X, padx=5, pady=4)
        
        self.participant_var = tk.StringVar(master=self.root, value=f"ID: {self.participant_id}")
        ttk.Label(pbox, textvariable=self.participant_var, font=fn_b).pack(pady=2)

        # Streams frame
        sbox = ttk.LabelFrame(main, text="LSL Streams", padding=10)
        sbox.pack(fill=tk.X, padx=5, pady=4)
        
        self.status_var = tk.StringVar(master=self.root, value="Initializing...")
        self.mstream_var = tk.StringVar(master=self.root, value="Not active")
        self.astream_var = tk.StringVar(master=self.root, value="Not active")
        self.adev_var = tk.StringVar(master=self.root, value="Not selected")
        
        lab = lambda r, c, t: ttk.Label(sbox, text=t).grid(row=r, column=c, sticky="w")
        lab(0, 0, "Status:"); ttk.Label(sbox, textvariable=self.status_var, font=fn_b)\
            .grid(row=0, column=1, sticky="w")
        lab(1, 0, "Mouse stream:"); ttk.Label(sbox, textvariable=self.mstream_var)\
            .grid(row=1, column=1, sticky="w")
        lab(2, 0, "Audio stream:"); ttk.Label(sbox, textvariable=self.astream_var)\
            .grid(row=2, column=1, sticky="w")
        lab(3, 0, "Audio input:"); ttk.Label(sbox, textvariable=self.adev_var)\
            .grid(row=3, column=1, sticky="w")
        
        # Audio capture checkbox
        self.audio_input_var = tk.BooleanVar(value=True)
        audio_check = ttk.Checkbutton(
            sbox, 
            text="Enable audio input streaming", 
            variable=self.audio_input_var,
            command=self.toggle_audio_input
        )
        audio_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=5)

        # Clicks frame
        cbox = ttk.LabelFrame(main, text="Mouse Clicks", padding=10)
        cbox.pack(fill=tk.X, padx=5, pady=4)
        
        self.ccount_var = tk.StringVar(master=self.root, value="0 recorded")
        self.lastclick_var = tk.StringVar(master=self.root, value="Last: –")
        
        ttk.Label(cbox, textvariable=self.ccount_var, font=("Arial", 12, "bold")).pack()
        ttk.Label(cbox, textvariable=self.lastclick_var).pack()
        
        # Controls
        controls_frame = ttk.Frame(cbox)
        controls_frame.pack(fill=tk.X, pady=5)
        
        self.stream_button = ttk.Button(
            controls_frame, 
            text="STOP STREAMING", 
            command=self.toggle_streaming,
            width=20
        )
        self.stream_button.pack(side=tk.LEFT, padx=5)
        
        self.test_button = ttk.Button(
            controls_frame,
            text="Test Tone",
            command=self.test_tone,
            width=10
        )
        self.test_button.pack(side=tk.LEFT, padx=5)
        
        # Tone settings frame
        tbox = ttk.LabelFrame(main, text="Tone Settings", padding=10)
        tbox.pack(fill=tk.X, padx=5, pady=4)
        
        # Frequency control
        freq_frame = ttk.Frame(tbox)
        freq_frame.pack(fill=tk.X, pady=2)
        
        ttk.Label(freq_frame, text="Frequency:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.freq_var = tk.IntVar(value=TONE_FREQUENCY)
        freq_scale = ttk.Scale(
            freq_frame,
            from_=200,
            to=2000,
            variable=self.freq_var,
            command=self._update_frequency
        )
        freq_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.freq_label = ttk.Label(freq_frame, text=f"{TONE_FREQUENCY} Hz", width=8)
        self.freq_label.pack(side=tk.LEFT)
        
        # Fixed volume label
        vol_frame = ttk.Frame(tbox)
        vol_frame.pack(fill=tk.X, pady=2)
        
        vol_percent = int(TONE_AMPLITUDE * 100)
        ttk.Label(vol_frame, text=f"Fixed volume: {vol_percent}%").pack(side=tk.LEFT, padx=(0, 5))

        # Log area
        lbox = ttk.LabelFrame(main, text="Log", padding=10)
        lbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=4)
        
        self.log_text = scrolledtext.ScrolledText(lbox, height=10, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, msg):
        """Add message to the log with timestamp"""
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        print(line)
        
        if not self.root.winfo_exists():
            return
            
        def _append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
            
        self.root.after(0, _append)

    # ───────────────────────── audio device search ──────────────────────
    def find_audio_device(self):
        """Find the Audio Input 3/4 device"""
        try:
            devices = sd.query_devices()
            
            matches = [(i, d) for i, d in enumerate(devices)
                       if AUDIO_IN_NAME.lower() in d["name"].lower() and
                          d["max_input_channels"] >= AUDIO_CHANNELS]
                          
            if not matches:
                self.adev_var.set("Not found")
                self.log(f"No audio input containing '{AUDIO_IN_NAME}'")
                return
                
            self.audio_device_id, info = matches[0]
            self.adev_var.set(f"{self.audio_device_id}: {info['name']}")
            self.log(f"Audio input → {info['name']} (ID {self.audio_device_id})")
        except Exception as e:
            self.adev_var.set("Error")
            self.log(f"find_audio_device error: {e}")

    # ───────────────────────── tone settings ────────────────────────────
    def _update_frequency(self, *args):
        """Update the tone frequency"""
        try:
            freq = self.freq_var.get()
            self.freq_label.config(text=f"{freq} Hz")
            self.tone_generator.frequency = freq
            self.tone_generator.tone_data = self.tone_generator._generate_tone()
            self.log(f"Tone frequency set to {freq} Hz")
        except Exception as e:
            self.log(f"Error updating frequency: {e}")
    
    def test_tone(self):
        """Play a test tone"""
        self.tone_generator.play_tone()
        self.log("Test tone played")

    def toggle_audio_input(self):
        """Toggle audio input streaming on/off"""
        self.audio_input_enabled = self.audio_input_var.get()
        
        if self.streaming:
            if self.audio_input_enabled and self.audio_device_id is not None:
                # Start audio streaming if it's not already running
                if not hasattr(self, 'audio_thread') or not self.audio_thread or not self.audio_thread.is_alive():
                    self.audio_thread = threading.Thread(target=self.stream_audio, daemon=True)
                    self.audio_thread.start()
                    self.log("Audio input streaming started")
            else:
                # Stop audio streaming
                self.audio_running = False
                if hasattr(self, 'audio_thread') and self.audio_thread and self.audio_thread.is_alive():
                    self.audio_thread.join(timeout=1.0)
                self.log("Audio input streaming stopped")

    # ───────────────────────── create LSL streams ───────────────────────
    def create_lsl_streams(self):
        """Create LSL streams for mouse clicks and possibly audio"""
        try:
            # Create a unique identifier
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Mouse stream (3 channels: time, x, y coordinates)
            m_info = StreamInfo(
                f"{LSL_STREAM_NAME}_{self.participant_id}_{ts}", 
                LSL_STREAM_TYPE, 
                3, 
                0,  # irregular sampling rate
                "float32", 
                f"mouse_{self.participant_id}_{ts}"
            )
            
            # Add participant metadata to stream info
            desc = m_info.desc()
            desc.append_child_value("participant_id", self.participant_id)
            for key, value in self.participant_metadata.items():
                if key != "participant_id":  # Already added
                    desc.append_child_value(key, str(value))
            
            self.mouse_stream = StreamOutlet(m_info)
            self.mstream_var.set(m_info.name())
            self.log(f"Mouse LSL stream created: {m_info.name()} for participant {self.participant_id}")
            
            # Audio stream if enabled
            if self.audio_input_enabled and self.audio_device_id is not None:
                a_info = StreamInfo(
                    f"Audio_{self.participant_id}_{ts}", 
                    "Audio",
                    AUDIO_CHANNELS, 
                    AUDIO_SAMPLE_RATE,
                    "float32", 
                    f"audio_{self.participant_id}_{ts}"
                )
                
                # Add participant metadata to audio stream info
                a_desc = a_info.desc()
                a_desc.append_child_value("participant_id", self.participant_id)
                for key, value in self.participant_metadata.items():
                    if key != "participant_id":  # Already added
                        a_desc.append_child_value(key, str(value))
                
                self.audio_stream = StreamOutlet(a_info)
                self.astream_var.set(a_info.name())
                self.log(f"Audio LSL stream created: {a_info.name()} for participant {self.participant_id}")
            else:
                self.audio_stream = None
                self.astream_var.set("Disabled")
                
            return True
        except Exception as e:
            self.log(f"Error creating LSL streams: {e}")
            return False

    # ───────────────────────── mouse polling ────────────────────────────
    def _cursor(self):
        """Get cursor position"""
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(byref(pt))
        return pt.x, pt.y
    
    def _buttons(self):
        """Get mouse button states"""
        U = ctypes.windll.user32.GetAsyncKeyState
        return (U(0x01) & 0x8000 != 0,  # Left button
                U(0x02) & 0x8000 != 0,  # Right button
                U(0x04) & 0x8000 != 0)  # Middle button

    def poll_mouse(self):
        """Thread function to poll mouse state"""
        try:
            while self.streaming:
                # Get button states
                l, r, m = self._buttons()
                
                # Check for button press events (transition from released to pressed)
                if l and not self.last_left:
                    self._click(0)  # Left button
                if r and not self.last_right:
                    self._click(1)  # Right button
                if m and not self.last_middle:
                    self._click(2)  # Middle button
                
                # Update previous states
                self.last_left, self.last_right, self.last_middle = l, r, m
                
                # Sleep for a short time
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            self.log(f"Mouse polling error: {e}")

    def _click(self, idx):
        """Handle a mouse click event"""
        # Get cursor position and timestamp
        x, y = self._cursor()
        t = time.perf_counter() - self.start_time
        
        # Stream the click data to LSL
        self.mouse_stream.push_sample([t, float(x), float(y)])
        
        # Update click count
        self.click_count += 1
        self.ccount_var.set(f"{self.click_count} recorded")
        
        # Update last click info
        button_name = "left" if idx == 0 else "right" if idx == 1 else "middle"
        self.lastclick_var.set(f"Last: ({x},{y}) {button_name} @ {t:.3f}s")
        
        # Play a tone
        self.tone_generator.play_tone()

    # ───────────────────────── audio streaming ─────────────────────────
    def stream_audio(self):
        """Thread function to stream audio from Input 3/4 to LSL"""
        if not (self.audio_stream and self.streaming and self.audio_device_id is not None):
            return
            
        self.audio_running = True
        audio_chunks_sent = 0
        device = self.audio_device_id
        sample_rate = AUDIO_SAMPLE_RATE
        
        # Verify device settings
        try:
            sd.check_input_settings(
                device=device,
                samplerate=sample_rate,
                channels=AUDIO_CHANNELS,
                dtype="float32"
            )
        except Exception as e:
            self.log(f"{sample_rate} Hz not accepted ({e}) → falling back to device default")
            sample_rate = None
        
        # Audio callback function
        def audio_callback(indata, frames, time_info, status):
            if status:
                self.log(f"Audio status: {status}")
                
            if not self.audio_running:
                raise sd.CallbackAbort
                
            # Push audio data to LSL
            self.audio_stream.push_chunk(indata.tolist())
            
            nonlocal audio_chunks_sent
            audio_chunks_sent += 1
            
            # Log progress occasionally
            if audio_chunks_sent % 50 == 0:
                self.log(f"Audio chunks sent: {audio_chunks_sent}")
        
        # Open input stream
        try:
            with sd.InputStream(
                device=device,
                channels=AUDIO_CHANNELS,
                samplerate=sample_rate,
                blocksize=AUDIO_CHUNK_SIZE,
                dtype="float32",
                callback=audio_callback
            ):
                self.log(f"Audio input stream opened (sr={sample_rate or 'device default'})")
                
                # Keep thread alive while streaming
                while self.audio_running and self.streaming:
                    sd.sleep(100)
                    
        except Exception as e:
            self.log(f"Audio input stream error: {e}")
            
        self.log("Audio streaming thread exited")
        self.audio_running = False

    # ───────────────────────── control flow ────────────────────────────
    def toggle_streaming(self):
        """Toggle streaming on/off"""
        if self.streaming:
            self.stop_streaming()
        else:
            self.start_streaming()

    def start_streaming(self):
        """Start streaming mouse clicks and audio"""
        if self.streaming:
            return
            
        # Create LSL streams
        if not self.create_lsl_streams():
            return
            
        # Start the system
        self.start_time = time.perf_counter()
        self.streaming = True
        self.status_var.set(f"Streaming for {self.participant_id}")
        
        # Start mouse polling thread
        self.mouse_thread = threading.Thread(target=self.poll_mouse, daemon=True)
        self.mouse_thread.start()
        
        # Start audio streaming if enabled
        if self.audio_input_enabled and self.audio_device_id is not None:
            self.audio_thread = threading.Thread(target=self.stream_audio, daemon=True)
            self.audio_thread.start()
        else:
            self.log("Audio input streaming disabled")
        
        # Update UI
        self.stream_button.config(text="STOP STREAMING")
        self.log(f"Streaming started for participant {self.participant_id}")

    def stop_streaming(self):
        """Stop all streaming"""
        if not self.streaming:
            return
            
        # Stop the system
        self.streaming = False
        self.audio_running = False
        
        # Wait for audio thread to finish
        if hasattr(self, 'audio_thread') and self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=1.0)
        
        # Update UI
        self.status_var.set("Stopped")
        self.stream_button.config(text="START STREAMING")
        
        # Calculate duration
        duration = time.perf_counter() - self.start_time
        self.log(f"Streaming stopped (duration: {duration:.1f}s)")

    def on_closing(self):
        """Handle window closing"""
        if self.streaming and not messagebox.askyesno("Quit", "Streaming active - quit anyway?"):
            return
            
        # Stop all streaming
        self.stop_streaming()
        
        # Clean up tone generator
        if hasattr(self, 'tone_generator'):
            self.tone_generator.cleanup()
        
        # Destroy the window
        self.root.destroy()

# ────────────────────────── main ──────────────────────────
if __name__ == "__main__":
    # Check platform
    if sys.platform != 'win32':
        print("This script only supports Windows for global mouse detection")
        sys.exit(1)
        
    # Create Tkinter root
    root = tk.Tk()
    
    # Create the application
    app = CombinedStreamer(root)
    
    # Start Tkinter event loop
    root.mainloop()