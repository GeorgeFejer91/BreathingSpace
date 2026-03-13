#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Combined LSL Streamer and Recorder
- Automatically reads participant ID from metadata files
- Streams mouse clicks and plays tones when mouse is clicked
- Optionally captures audio from Input 3/4 to LSL
- Automatically finds and records all LSL streams containing the participant ID
- Positions itself in the bottom left of the screen
- Auto-starts all functionality on launch

Requires: pylsl, liesl, sounddevice, numpy
"""

import os
import sys
import time
import json
import glob
import threading
import datetime as dt
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import ctypes
import numpy as np
from ctypes import wintypes, byref

try:
    from pylsl import StreamInfo, StreamOutlet, resolve_streams
    from liesl import Recorder
    import sounddevice as sd
except ImportError as e:
    print("Missing libraries – install with:\n  pip install pylsl liesl sounddevice numpy")
    sys.exit(1)

# Set sounddevice for low latency
sd.default.latency = 'low'

# ──────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────
# File paths
BASE_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment"
METADATA_DIR = os.path.join(BASE_DIR, "Results", "participant_metadata")
RECORDING_DIR = os.path.join(BASE_DIR, "Results", "LSL_Output")

# LSL Configuration
LSL_STREAM_NAME = "MouseClicks"
LSL_STREAM_TYPE = "MouseEvents"
AUDIO_SAMPLE_RATE = 44100  # Hz (fallback to device default if invalid)
AUDIO_CHANNELS = 2         # stereo
AUDIO_CHUNK_SIZE = 1024    # frames / block
AUDIO_IN_NAME = "Input 3/4"  # Audio input device name substring
AUDIO_OUT_NAME = "Output 3/4" # Audio output device name substring

# Tone Configuration
TONE_FREQUENCY = 1000       # Hz
TONE_DURATION = 0.1         # seconds
TONE_AMPLITUDE = 0.25       # 0.0-1.0

# Mouse Polling
POLL_INTERVAL = 0.005       # seconds

# Ensure directories exist
os.makedirs(METADATA_DIR, exist_ok=True)
os.makedirs(RECORDING_DIR, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
#  Helper Functions
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
#  Tone Generator Class
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
#  Combined Application
# ──────────────────────────────────────────────────────────────────────────────
class CombinedLSLApp:
    """Combined LSL Streamer and Recorder Application"""
    def __init__(self, root):
        self.root = root
        self.root.title("LSL Streamer & Recorder")
        
        # Make window stay on top
        self.root.attributes("-topmost", True)
        
        # Position window in bottom left of screen
        self.position_window()
        
        # Try to get 1ms timers on Windows
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
        
        # Retrieve participant information
        self.participant_id, self.participant_metadata = get_participant_info()
        
        # Streamer state
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
        
        # Stream names for verification
        self.mouse_stream_name = ""
        self.audio_stream_name = ""
        
        # Recorder state
        self.recorder = None
        self.recording = False
        self.matched_streams = []
        self.verified_streams = False
        
        # Tone generator
        self.tone_generator = ToneGenerator()
        
        # Build the UI
        self._build_ui()
        
        # Find audio input device
        self.find_audio_device()
        
        # Auto-start everything
        self.root.after(1000, self.auto_start)
    
    def position_window(self):
        """Position window on the right side of the screen with 75% height"""
        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Set window to take up full right side with 75% height
        window_width = int(screen_width * 0.5)  # 50% of screen width
        window_height = int(screen_height * 0.75)  # 75% of screen height
        
        # Position at right side, vertically centered
        x_position = screen_width - window_width
        y_position = int((screen_height - window_height) / 2)  # Center vertically
        
        # Set window size and position
        self.root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
    
    def _build_ui(self):
        """Build the user interface"""
        # Main frame with minimal padding
        main = ttk.Frame(self.root, padding=3)
        main.pack(fill=tk.BOTH, expand=True)
        
        # Top section with participant info and essential controls
        top_frame = ttk.Frame(main)
        top_frame.pack(fill=tk.X, padx=2, pady=2)
        
        # Participant info
        participant_frame = ttk.LabelFrame(top_frame, text="Participant", padding=2)
        participant_frame.pack(side=tk.LEFT, fill=tk.Y, padx=2)
        
        self.participant_var = tk.StringVar(value=f"ID: {self.participant_id}")
        ttk.Label(participant_frame, textvariable=self.participant_var, 
                  font=("Arial", 10, "bold")).pack(padx=5, pady=2)
        
        # Control buttons in a separate frame
        control_frame = ttk.LabelFrame(top_frame, text="Controls", padding=2)
        control_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=2)
        
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(padx=5, pady=2)
        
        self.stop_button = ttk.Button(button_frame, text="STOP ALL", 
                                     command=self.stop_all, width=10)
        self.stop_button.pack(side=tk.LEFT, padx=2)
        
        self.refresh_button = ttk.Button(button_frame, text="Refresh", 
                                        command=self.refresh_and_restart, width=10)
        self.refresh_button.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(button_frame, text="Test Tone", 
                  command=self.test_tone, width=10).pack(side=tk.LEFT, padx=2)
        
        # Create a notebook with tabs for more compact organization
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=5)
        
        # Add tabs
        self._build_overview_tab()
        self._build_streams_tab()
        self._build_log_tab()
        
        # Status bar at bottom
        status_frame = ttk.Frame(main)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=2, pady=2)
        
        # Status display
        self.status_var = tk.StringVar(value="Initializing...")
        status_label = ttk.Label(status_frame, textvariable=self.status_var, font=("Arial", 9))
        status_label.pack(side=tk.LEFT, padx=2)
        
        # Click counter on status bar
        self.click_var = tk.StringVar(value="Clicks: 0")
        ttk.Label(status_frame, textvariable=self.click_var, font=("Arial", 9)).pack(side=tk.RIGHT, padx=10)
    
    def _build_overview_tab(self):
        """Build the overview tab with essential information"""
        overview_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(overview_frame, text="Overview")
        
        # Stream status in a larger frame
        stream_frame = ttk.LabelFrame(overview_frame, text="Stream Status", padding=5)
        stream_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Use a grid for better organization of status indicators
        grid = ttk.Frame(stream_frame)
        grid.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Column headers
        ttk.Label(grid, text="Stream Type", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ttk.Label(grid, text="Status", font=("Arial", 10, "bold")).grid(row=0, column=1, sticky="w", padx=10, pady=5)
        
        # Stream status indicators with larger text and spacing
        ttk.Label(grid, text="Mouse Stream:", font=("Arial", 10)).grid(row=1, column=0, sticky="w", padx=10, pady=8)
        self.mouse_status = tk.StringVar(value="Inactive")
        ttk.Label(grid, textvariable=self.mouse_status, font=("Arial", 10)).grid(row=1, column=1, sticky="w", padx=10, pady=8)
        
        ttk.Label(grid, text="Audio Stream:", font=("Arial", 10)).grid(row=2, column=0, sticky="w", padx=10, pady=8)
        self.audio_status = tk.StringVar(value="Inactive")
        ttk.Label(grid, textvariable=self.audio_status, font=("Arial", 10)).grid(row=2, column=1, sticky="w", padx=10, pady=8)
        
        ttk.Label(grid, text="Recording:", font=("Arial", 10)).grid(row=3, column=0, sticky="w", padx=10, pady=8)
        self.recording_status = tk.StringVar(value="Inactive")
        ttk.Label(grid, textvariable=self.recording_status, font=("Arial", 10)).grid(row=3, column=1, sticky="w", padx=10, pady=8)
        
        ttk.Label(grid, text="Verification:", font=("Arial", 10)).grid(row=4, column=0, sticky="w", padx=10, pady=8)
        self.verification_status = tk.StringVar(value="Not verified")
        ttk.Label(grid, textvariable=self.verification_status, font=("Arial", 10)).grid(row=4, column=1, sticky="w", padx=10, pady=8)
        
        # Instructions frame
        instructions_frame = ttk.LabelFrame(overview_frame, text="Quick Instructions", padding=5)
        instructions_frame.pack(fill=tk.X, pady=5)
        
        instructions_text = (
            "• The system automatically starts streaming and recording for the detected participant.\n"
            "• Mouse clicks are streamed to LSL and a tone is played on Output 3/4.\n"
            "• Audio from Input 3/4 is streamed to LSL.\n"
            "• Use 'Refresh' to update participant ID and restart streaming.\n"
            "• Use 'STOP ALL' to halt all operations."
        )
        
        ttk.Label(instructions_frame, text=instructions_text, justify=tk.LEFT, wraplength=600).pack(pady=5)
    
    def _build_streams_tab(self):
        """Build the streams tab showing discovered LSL streams"""
        streams_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(streams_frame, text="Streams")
        
        # Stream list section with title
        ttk.Label(streams_frame, text="Discovered LSL Streams:", 
                 font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(5, 2))
        
        # Explanation text
        explanation = (
            "Streams with 'MOUSE STREAM' or 'AUDIO STREAM' are our generated streams.\n"
            "Streams with 'MATCHED' are identified as belonging to this participant.\n"
            "All matched streams will be recorded."
        )
        ttk.Label(streams_frame, text=explanation, wraplength=600).pack(anchor=tk.W, pady=(0, 5))
        
        # Stream listbox frame with border
        listbox_frame = ttk.LabelFrame(streams_frame, text="Active Streams")
        listbox_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Stream listbox with larger font and height
        self.stream_listbox = tk.Listbox(listbox_frame, height=12, font=("Consolas", 9))
        self.stream_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Add scrollbar
        scrollbar = ttk.Scrollbar(self.stream_listbox, orient=tk.VERTICAL, command=self.stream_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.stream_listbox.config(yscrollcommand=scrollbar.set)
        
        # Manual scan button
        scan_button = ttk.Button(streams_frame, text="Rescan Streams", command=self.scan_streams, width=15)
        scan_button.pack(anchor=tk.E, pady=5)
    
    def _build_log_tab(self):
        """Build the log tab for messages"""
        log_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(log_frame, text="Log")
        
        # Controls for log
        control_frame = ttk.Frame(log_frame)
        control_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(control_frame, text="Operation Log:", 
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        
        clear_button = ttk.Button(control_frame, text="Clear Log", 
                                 command=lambda: self.log_text.delete(1.0, tk.END), width=10)
        clear_button.pack(side=tk.RIGHT)
        
        # Log text area with larger size and better formatting
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, 
                                                font=("Consolas", 9), wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("error", foreground="red")
        self.log_text.tag_configure("success", foreground="green")
        self.log_text.tag_configure("warning", foreground="orange")
        
        # Initial log entry
        self.log_text.insert(tk.END, "Log initialized. Application starting...\n")
        self.log_text.config(state=tk.DISABLED)
    
    def log(self, msg):
        """Add message to the log with timestamp"""
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        print(line)
        
        if not self.root.winfo_exists():
            return
            
        def _append():
            try:
                self.log_text.config(state=tk.NORMAL)
                
                # Apply color tags based on message content
                if "ERROR" in msg.upper() or "FAIL" in msg.upper():
                    self.log_text.insert(tk.END, line + "\n", "error")
                elif "SUCCESS" in msg.upper() or "VERIFIED" in msg.upper():
                    self.log_text.insert(tk.END, line + "\n", "success")
                elif "WARNING" in msg.upper():
                    self.log_text.insert(tk.END, line + "\n", "warning")
                else:
                    self.log_text.insert(tk.END, line + "\n")
                    
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
            except Exception:
                pass
            
        self.root.after(0, _append)
    
    def find_audio_device(self):
        """Find the Audio Input 3/4 device"""
        try:
            devices = sd.query_devices()
            
            matches = [(i, d) for i, d in enumerate(devices)
                       if AUDIO_IN_NAME.lower() in d["name"].lower() and
                          d["max_input_channels"] >= AUDIO_CHANNELS]
                          
            if not matches:
                self.audio_status.set("Not found")
                self.log(f"No audio input containing '{AUDIO_IN_NAME}'")
                return
                
            self.audio_device_id, info = matches[0]
            self.audio_status.set(f"Found: {info['name']}")
            self.log(f"Audio input → {info['name']} (ID {self.audio_device_id})")
        except Exception as e:
            self.audio_status.set("Error")
            self.log(f"Find audio device error: {e}")
    
    def scan_streams(self):
        """Scan for LSL streams and filter by participant ID"""
        self.status_var.set("Scanning streams...")
        
        # Clear streams list but keep any entries in the listbox
        self.matched_streams = []
        
        # Get participant ID without the "P" prefix
        participant_num = self.participant_id.replace("P", "").strip()
        
        try:
            # Get active streams with a timeout
            all_streams = resolve_streams(wait_time=2.0)
            
            if not all_streams:
                self.status_var.set("No streams found")
                self.log("No LSL streams found during scan")
                return False
            
            # Add our specific streams first to ensure they're included
            found_mouse = False
            found_audio = False
            
            # First pass: find our specific streams
            for stream in all_streams:
                if stream.name() == self.mouse_stream_name:
                    self.matched_streams.append(stream)
                    self.stream_listbox.insert(tk.END, f"{stream.name()} ({stream.type()}) - MOUSE STREAM")
                    found_mouse = True
                    
                elif stream.name() == self.audio_stream_name:
                    self.matched_streams.append(stream)
                    self.stream_listbox.insert(tk.END, f"{stream.name()} ({stream.type()}) - AUDIO STREAM")
                    found_audio = True
            
            # Report on our specific streams
            if not found_mouse:
                self.log("WARNING: Our mouse stream was not found in scan")
            if not found_audio:
                self.log("WARNING: Our audio stream was not found in scan")
            
            # Filter remaining streams for participant ID
            for stream in all_streams:
                # Skip if already added
                if stream.name() == self.mouse_stream_name or stream.name() == self.audio_stream_name:
                    continue
                    
                stream_name = stream.name().lower()
                if participant_num in stream_name or f"p{participant_num}" in stream_name:
                    self.matched_streams.append(stream)
                    self.stream_listbox.insert(tk.END, f"{stream.name()} ({stream.type()}) - MATCHED")
                else:
                    # Also show non-matched streams for visibility
                    self.stream_listbox.insert(tk.END, f"{stream.name()} ({stream.type()})")
            
            # Check if we have our critical streams
            if not (found_mouse and found_audio):
                self.log("ERROR: Could not find both of our streams during scan")
                self.status_var.set("Missing our streams")
                return False
            
            # Update status
            self.status_var.set(f"{len(self.matched_streams)} streams ready")
            return True
                
        except Exception as e:
            self.log(f"Stream scan error: {e}")
            self.status_var.set("Scan error")
        
        return False
    
    def test_tone(self):
        """Play a test tone"""
        self.tone_generator.play_tone()
        self.log("Test tone played")
    
    def verify_streams(self):
        """Verify that our created streams are discoverable via LSL"""
        self.log("Verifying streams can be discovered...")
        self.verification_status.set("Verifying...")
        self.verified_streams = False
        
        # Give streams time to register with LSL
        time.sleep(0.5)
        
        try:
            # Get all available LSL streams
            all_streams = resolve_streams(wait_time=2.0)
            
            if not all_streams:
                self.log("No LSL streams found during verification")
                self.verification_status.set("Failed: No streams")
                return False
            
            # Check if our streams are among the discovered streams
            mouse_found = False
            audio_found = False
            
            for stream in all_streams:
                if stream.name() == self.mouse_stream_name:
                    mouse_found = True
                    self.log(f"Mouse stream verified: {self.mouse_stream_name}")
                
                if stream.name() == self.audio_stream_name:
                    audio_found = True
                    self.log(f"Audio stream verified: {self.audio_stream_name}")
            
            if mouse_found and audio_found:
                self.log("All streams successfully verified")
                self.verification_status.set("Success")
                self.verified_streams = True
                return True
            else:
                missing = []
                if not mouse_found:
                    missing.append("mouse")
                if not audio_found:
                    missing.append("audio")
                
                self.log(f"Verification failed: {', '.join(missing)} stream(s) not found")
                self.verification_status.set(f"Failed: {', '.join(missing)} missing")
                return False
                
        except Exception as e:
            self.log(f"Error verifying streams: {e}")
            self.verification_status.set("Error")
            return False
    
    def create_lsl_streams(self):
        """Create LSL streams for mouse clicks and audio"""
        try:
            # Create a unique identifier
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Mouse stream (3 channels: time, x, y coordinates)
            self.mouse_stream_name = f"{LSL_STREAM_NAME}_{self.participant_id}_{ts}"
            m_info = StreamInfo(
                self.mouse_stream_name, 
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
            self.mouse_status.set(f"Active: {self.mouse_stream_name}")
            self.log(f"Mouse LSL stream created: {self.mouse_stream_name}")
            
            # Check if we have an audio device
            if self.audio_device_id is None:
                self.log("ERROR: No audio input device found, can't continue without audio")
                self.audio_status.set("Not Found")
                return False
            
            # Audio stream (now mandatory)
            self.audio_stream_name = f"Audio_{self.participant_id}_{ts}"
            a_info = StreamInfo(
                self.audio_stream_name, 
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
            self.audio_status.set(f"Active: {self.audio_stream_name}")
            self.log(f"Audio LSL stream created: {self.audio_stream_name}")
            
            # Verify the streams are discoverable
            return self.verify_streams()
            
        except Exception as e:
            self.log(f"Error creating LSL streams: {e}")
            return False
    
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
        self.click_var.set(f"Clicks: {self.click_count}")
        
        # Update last click info in log
        button_name = "left" if idx == 0 else "right" if idx == 1 else "middle"
        self.log(f"Click: {button_name} @ {t:.3f}s ({x},{y})")
        
        # Play a tone
        self.tone_generator.play_tone()
    
    def stream_audio(self):
        """Thread function to stream audio from Input 3/4 to LSL"""
        if not (self.audio_stream and self.streaming and self.audio_device_id is not None):
            self.log("Cannot start audio streaming - missing requirements")
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
            if status and status.output_underflow:
                # Don't log buffer underflows as they're common
                pass
            elif status:
                self.log(f"Audio status: {status}")
                
            if not self.audio_running:
                raise sd.CallbackAbort
                
            # Push audio data to LSL
            try:
                self.audio_stream.push_chunk(indata.tolist())
                
                nonlocal audio_chunks_sent
                audio_chunks_sent += 1
                
                # Log progress occasionally
                if audio_chunks_sent % 200 == 0:  # Less frequent logging
                    self.log(f"Audio: {audio_chunks_sent} chunks sent")
            except Exception as e:
                self.log(f"Error pushing audio chunk: {e}")
        
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
            self.audio_status.set("Error")
            
        self.log("Audio streaming thread exited")
        self.audio_running = False
    
    def start_recorder(self):
        """Start the LSL recorder in a separate thread"""
        if self.recording:
            self.log("Recording already active")
            return
        
        # Verify that streams are available before starting recording
        if not self.verified_streams:
            self.log("Cannot start recording - streams not verified")
            self.recording_status.set("Error: Not verified")
            return
        
        # Check that we have found our streams to record
        if not self.matched_streams or len(self.matched_streams) < 2:
            self.log("Cannot start recording - streams not found")
            self.recording_status.set("Error: No streams")
            return
            
        threading.Thread(target=self._recording_thread, daemon=True).start()
    
    def _recording_thread(self):
        """Thread to handle LSL recording"""
        try:
            # Prepare output file path
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = os.path.join(RECORDING_DIR, f"{self.participant_id}_{timestamp}.xdf")
            
            # Create streamargs list with exact streams we want to record
            streamargs = []
            for stream in self.matched_streams:
                if stream.name() == self.mouse_stream_name or stream.name() == self.audio_stream_name:
                    streamargs.append({
                        "name": stream.name(),
                        "type": stream.type(),
                        "source_id": stream.source_id()
                    })
                    self.log(f"Recording stream: {stream.name()} ({stream.type()})")
            
            # Verify we found both our streams
            if len(streamargs) < 2:
                self.log(f"Error: Only found {len(streamargs)} of our streams to record")
                self.recording_status.set(f"Error: {len(streamargs)}/2 streams")
                return
            
            # Create recorder and start recording
            self.recorder = Recorder()
            self.recorder.start_recording(filename=output_filename, streamargs=streamargs)
            
            # Update status
            self.recording = True
            self.recording_status.set(f"Active: {len(streamargs)} streams")
            self.log(f"Recording started to {output_filename}")
            
        except Exception as e:
            self.log(f"Error starting recording: {e}")
            self.recording_status.set("Error: " + str(e)[:20])
    
    def stop_recorder(self):
        """Stop the LSL recorder"""
        if not self.recording:
            return
        
        if not self.recorder:
            self.log("No active recorder to stop")
            self.recording = False
            self.recording_status.set("Inactive")
            return
        
        try:
            # Give additional time for any remaining data to be written
            time.sleep(0.5)
            
            # Stop the recorder properly
            self.recorder.stop_recording()
            self.log("Recording stopped")
            
        except Exception as e:
            self.log(f"Error stopping recording: {e}")
        finally:
            # Always clean up resources regardless of errors
            self.recording = False
            self.recorder = None
            self.recording_status.set("Inactive")
            
            # Add extra delay to ensure complete cleanup
            time.sleep(0.5)
    
    def start_streaming(self):
        """Start streaming mouse clicks and audio"""
        if self.streaming:
            self.log("Streaming already active")
            return
        
        # Reset verification status
        self.verification_status.set("Not verified")
        self.verified_streams = False
            
        # Create LSL streams and verify they're discoverable
        if not self.create_lsl_streams():
            self.log("Failed to create and verify streams - cannot start streaming")
            self.status_var.set("Stream creation failed")
            return
            
        # Start the system
        self.start_time = time.perf_counter()
        self.streaming = True
        self.status_var.set(f"Streaming: {self.participant_id}")
        
        # Start mouse polling thread
        self.mouse_thread = threading.Thread(target=self.poll_mouse, daemon=True)
        self.mouse_thread.start()
        
        # Start audio streaming (now mandatory)
        if self.audio_device_id is not None:
            self.audio_thread = threading.Thread(target=self.stream_audio, daemon=True)
            self.audio_thread.start()
        else:
            self.log("ERROR: Audio device not found")
            self.audio_status.set("Error: Device not found")
            # Don't continue without audio
            self.stop_streaming()
            return
        
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
        self.mouse_status.set("Inactive")
        self.audio_status.set("Inactive")
        
        # Calculate duration
        if hasattr(self, 'start_time') and self.start_time:
            duration = time.perf_counter() - self.start_time
            self.log(f"Streaming stopped (duration: {duration:.1f}s)")
        else:
            self.log("Streaming stopped")
        
        # Add slight delay to ensure streams are properly closed
        time.sleep(0.5)
    
    def auto_start(self):
        """Automatically start streaming and recording"""
        # Check if participant ID is valid
        if self.participant_id in ["Unknown", "Error"]:
            self.status_var.set("No valid participant ID - please refresh")
            return
        
        # Clear the stream listbox
        self.stream_listbox.delete(0, tk.END)
        
        # Start streaming (includes stream creation and verification)
        self.start_streaming()
        
        # Only continue if streaming started successfully
        if not self.streaming or not self.verified_streams:
            self.log("Cannot continue with auto-start - streaming or verification failed")
            return
            
        # Scan for streams to include in recording
        if self.scan_streams():
            # Wait a moment to ensure all streams are properly registered
            time.sleep(1.0)
            
            # Start recording
            self.start_recorder()
            
            # Update status
            self.status_var.set(f"Running: {self.participant_id}")
        else:
            self.log("Stream scanning failed - cannot start recording")
    
    def refresh_and_restart(self):
        """Refresh participant ID and restart"""
        # Show status
        self.status_var.set("Refreshing...")
        
        # Stop everything first
        self.stop_all()
        
        # Add a delay to ensure proper cleanup
        time.sleep(1.0)
        
        # Refresh participant ID
        self.participant_id, self.participant_metadata = get_participant_info()
        self.participant_var.set(f"Participant: {self.participant_id}")
        
        # Reset counters and status
        self.click_count = 0
        self.click_var.set("Clicks: 0")
        self.verification_status.set("Not verified")
        self.verified_streams = False
        
        # Clear stream list
        self.stream_listbox.delete(0, tk.END)
        
        # Check and restart
        if self.participant_id not in ["Unknown", "Error"]:
            # Use after to ensure UI updates before auto-start
            self.root.after(500, self.auto_start)
        else:
            self.status_var.set("No valid participant ID found")
    
    def stop_all(self):
        """Stop all streaming and recording"""
        # Stop recording first to ensure data is saved
        if self.recording:
            self.log("Stopping recording...")
            self.stop_recorder()
            # Add delay to ensure complete shutdown
            time.sleep(0.5)
        
        # Then stop streaming
        if self.streaming:
            self.log("Stopping streaming...")
            self.stop_streaming()
            # Add delay to ensure complete shutdown
            time.sleep(0.5)
        
        # Reset verification status
        self.verification_status.set("Not verified")
        self.verified_streams = False
        
        # Update status
        self.status_var.set("Stopped")
    
    def on_closing(self):
        """Handle window closing"""
        if (self.streaming or self.recording) and not messagebox.askyesno("Quit", "Recording active - quit anyway?"):
            return
        
        # Show closing status
        self.status_var.set("Closing...")
        self.root.update()
            
        # Stop everything
        self.stop_all()
        
        # Clean up tone generator
        if hasattr(self, 'tone_generator'):
            self.tone_generator.cleanup()
        
        # Final cleanup
        try:
            # Ensure sounddevice is completely stopped
            sd.stop()
        except:
            pass
            
        # Destroy the window
        self.root.destroy()

# ────────────────────────── main ──────────────────────────
def main():
    # Check platform
    if sys.platform != 'win32':
        print("This script requires Windows for global mouse detection")
        sys.exit(1)
        
    # Create Tkinter root
    root = tk.Tk()
    
    # Create the application
    app = CombinedLSLApp(root)
    
    # Set close handler
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    # Start Tkinter event loop
    root.mainloop()

if __name__ == "__main__":
    main()