#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Enhanced PPS Experiment Runner with Auto-Focus and Mouse Centering

Features:
- Loading and playing two perfectly synchronized audio files
- Custom audio device selection (Output 1/2 for looming, "woojer" for tactile)
- Custom start time for fast-forwarding in the experiment
- Window always comes to front when tactile stimulus occurs
- Mouse auto-centers in the click area for each tactile stimulus
- Real-time reaction time tracking in CSV
- LSL streams for mouse clicks and audio events (with participant ID)
- Visual timeline of events
- Millisecond-precision reaction time tracking
- Adaptive recovery of missed trials at experiment end

Dependencies:
- Required: numpy, pandas, sounddevice, soundfile, tkinter
- Optional: pyautogui (for mouse centering)
- Optional: pylsl (for LSL streaming)
- Optional: pywin32 (for better window management on Windows)

Installation:
    pip install numpy pandas sounddevice soundfile pyautogui pylsl
    pip install pywin32  # Windows only

Usage:
    python pps_experiment.py

Author: AI Assistant
"""

import os
import time
import tkinter as tk
from tkinter import ttk, messagebox
import sounddevice as sd
import soundfile as sf
import numpy as np
import pandas as pd
import threading
import datetime
import re
import glob
import traceback
from pathlib import Path
import json

# Add mouse control support
try:
    import pyautogui
    MOUSE_CONTROL_AVAILABLE = True
except ImportError:
    print("WARNING: pyautogui not installed. Auto mouse centering will be disabled.")
    print("To enable, install with: pip install pyautogui")
    MOUSE_CONTROL_AVAILABLE = False

# Add LSL support
try:
    import pylsl
    LSL_AVAILABLE = True
except ImportError:
    print("WARNING: pylsl not installed. LSL streaming will be disabled.")
    print("To enable, install with: pip install pylsl")
    LSL_AVAILABLE = False

# For Windows-specific window management
import platform
if platform.system() == 'Windows':
    try:
        import win32gui
        import win32con
        WINDOWS_GUI_AVAILABLE = True
    except ImportError:
        print("WARNING: pywin32 not installed. Some window management features will be limited.")
        print("To enable, install with: pip install pywin32")
        WINDOWS_GUI_AVAILABLE = False
else:
    WINDOWS_GUI_AVAILABLE = False

# Configuration
BASE_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace"
EXPERIMENT_AUDIO_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentAudio")
EXPERIMENT_LOG_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentLog")
RESULTS_DIR = os.path.join(BASE_DIR, "Level2_RunExperiment", "Results")

# Response window in seconds
RESPONSE_WINDOW = 4.0  # Changed to 4 seconds

# Time before tactile stimulus to prepare centering (seconds)
PREPARE_BEFORE_TACTILE = 0.5

# Ensure results directory exists
os.makedirs(RESULTS_DIR, exist_ok=True)

class EnhancedExperimentRunner:
    def __init__(self):
        # Initialize variables
        self.participant_id = None
        self.available_participants = []
        self.experiment_running = False
        self.start_time = None
        self.audio_start_time = None
        self.mouse_clicks = []
        self.tactile_times = []
        self.timeline_markers1 = []
        self.timeline_markers2 = []
        self.click_count = 0
        self.audio_duration = 0
        self.design_df = None
        self.results_df = None
        self.results_file = None
        self.participant_results_dir = None
        self.recovery_phase = False
        self.recovery_checked = False
        self.start_offset_minutes = 0.0  # Default to starting at the beginning
        self.next_tactile_processed = set()  # Keep track of which tactile stimuli we've already centered the mouse for
        
        # Audio device tracking
        self.looming_device_id = None
        self.tactile_device_id = None
        
        # LSL streams
        self.lsl_mouse_stream = None
        self.lsl_audio_stream = None
        self.lsl_looming_stream = None
        self.lsl_tactile_stream = None
        
        # Flag to control audio playback
        self.stop_audio = False
        
        # Detect and select audio devices
        self.detect_audio_devices()
        
        # Scan for available participants
        self.scan_available_participants()
        
        # Initialize LSL if available
        if LSL_AVAILABLE:
            self.initialize_lsl_streams()
        
        # Create GUI
        self.create_gui()
        
        # Make window appear on top at startup
        self.bring_window_to_front()

    def detect_audio_devices(self):
        """
        Detect all available audio devices and select appropriate ones for
        looming (Output 1/2) and tactile (contains 'woojer') stimuli.
        """
        try:
            # Get all available devices
            devices = sd.query_devices()
            print("\n===== AVAILABLE AUDIO DEVICES =====")
            
            # Initialize device IDs to None (default device will be used if not found)
            self.looming_device_id = None
            self.tactile_device_id = None
            
            # Print all devices and search for target devices
            for i, device in enumerate(devices):
                is_output = device['max_output_channels'] > 0
                device_name = device['name'].lower()
                
                # Print device info
                output_str = "OUTPUT" if is_output else "INPUT"
                print(f"[{i}] {device['name']} ({output_str}, {device['max_output_channels']} channels)")
                
                # Only consider output devices
                if is_output:
                    # Check for looming device ("Output 1/2")
                    if "output 1/2" in device_name:
                        self.looming_device_id = i
                        print(f"  ✓ SELECTED FOR LOOMING AUDIO")
                    
                    # Check for tactile device (contains "woojer")
                    elif "woojer" in device_name:
                        self.tactile_device_id = i
                        print(f"  ✓ SELECTED FOR TACTILE STIMULATION")
            
            # Use default device if specific ones are not found
            if self.looming_device_id is None:
                print("Warning: No device containing 'Output 1/2' found for looming audio.")
                print("         Using default output device instead.")
                # Find default output device
                default_device = sd.query_devices(kind='output')
                self.looming_device_id = sd.default.device[1]  # Default output device
            
            if self.tactile_device_id is None:
                print("Warning: No device containing 'woojer' found for tactile stimulation.")
                print("         Using default output device instead.")
                self.tactile_device_id = sd.default.device[1]  # Default output device
            
            print(f"\nDevice selections:")
            print(f"  - Looming audio: Device {self.looming_device_id}")
            print(f"  - Tactile stimulation: Device {self.tactile_device_id}")
            print("====================================\n")
            
        except Exception as e:
            print(f"Error detecting audio devices: {e}")
            traceback.print_exc()
            # Fall back to default devices
            self.looming_device_id = None
            self.tactile_device_id = None

    def bring_window_to_front(self):
        """
        Bring this window to the front and make it the active window.
        Uses different methods depending on platform availability.
        """
        # Try to make window appear on top of all other windows
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.update()
        
        # Then allow it to go behind other windows if the user switches applications
        self.root.attributes("-topmost", False)
        
        # For Windows, use win32gui for more reliable focus
        if WINDOWS_GUI_AVAILABLE:
            try:
                # Get the window handle
                hwnd = win32gui.GetParent(self.root.winfo_id())
                
                # Activate the window and bring it to the foreground
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                print("Window brought to front using Windows API")
            except Exception as e:
                print(f"Error bringing window to front with Windows API: {e}")
                traceback.print_exc()
        else:
            # Focus on the window (works on most platforms)
            self.root.focus_force()
            print("Window brought to front using Tkinter")

    def center_mouse_in_click_area(self):
        """
        Center the mouse cursor in the click area of the GUI.
        Only works if pyautogui is available.
        """
        if not MOUSE_CONTROL_AVAILABLE:
            return
            
        try:
            # Get the geometry of the click canvas
            canvas_x = self.click_canvas.winfo_rootx()  # x position on screen
            canvas_y = self.click_canvas.winfo_rooty()  # y position on screen
            canvas_width = self.click_canvas.winfo_width()
            canvas_height = self.click_canvas.winfo_height()
            
            # Calculate center coordinates
            center_x = canvas_x + (canvas_width // 2)
            center_y = canvas_y + (canvas_height // 2)
            
            # Move mouse to center position
            pyautogui.moveTo(center_x, center_y)
            print(f"Mouse centered at screen position: {center_x}, {center_y}")
            
            # Log marker for debugging
            if LSL_AVAILABLE and self.lsl_audio_stream:
                self.send_lsl_audio_marker(f"mouse_centered_x{center_x}_y{center_y}")
                
        except Exception as e:
            print(f"Error centering mouse: {e}")
            traceback.print_exc()

    def process_upcoming_tactile(self, current_time):
        """
        Check if a tactile stimulus is about to occur and prepare the GUI.
        Centers the mouse and brings the window to front.
        
        Args:
            current_time: Current time in the audio playback (in seconds)
        """
        if not self.experiment_running or not self.tactile_times:
            return False
            
        # Find the next upcoming tactile stimulus
        next_tactile = None
        
        for t_time in sorted(self.tactile_times):
            # Only consider tactile times that haven't been processed yet
            if t_time > current_time and t_time < current_time + PREPARE_BEFORE_TACTILE:
                if t_time not in self.next_tactile_processed:
                    next_tactile = t_time
                    break
        
        if next_tactile:
            print(f"Preparing for upcoming tactile at {next_tactile:.3f}s (current: {current_time:.3f}s)")
            
            # Mark this tactile time as processed
            self.next_tactile_processed.add(next_tactile)
            
            # Bring window to front
            self.root.after(0, self.bring_window_to_front)
            
            # Center mouse cursor in click area
            self.root.after(0, self.center_mouse_in_click_area)
            
            return True
        
        return False

    def initialize_lsl_streams(self, update_participant_id=None):
        """
        Initialize LSL streams for experiment events.
        
        Args:
            update_participant_id: If provided, updates stream names with participant ID
        """
        try:
            # Current timestamp for stream names
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Determine participant identifier for stream names
            participant_str = ""
            if update_participant_id is not None:
                participant_str = f"P{update_participant_id}_"
            elif self.participant_id is not None:
                participant_str = f"P{self.participant_id}_"
            
            # If we're just updating the existing streams with participant ID
            if update_participant_id is not None and self.lsl_mouse_stream is not None:
                print(f"Updating LSL stream names with participant ID: {update_participant_id}")
                # We can't rename existing streams, so we'll close and recreate them
                if self.lsl_mouse_stream:
                    del self.lsl_mouse_stream
                if self.lsl_audio_stream:
                    del self.lsl_audio_stream
                if hasattr(self, 'lsl_looming_stream') and self.lsl_looming_stream:
                    del self.lsl_looming_stream
                if hasattr(self, 'lsl_tactile_stream') and self.lsl_tactile_stream:
                    del self.lsl_tactile_stream
            
            # Stream for mouse clicks
            mouse_info = pylsl.StreamInfo(
                name=f"{participant_str}PPS_MouseClicks_{timestamp}",
                type="Markers",
                channel_count=3,  # time, x, y
                nominal_srate=0,  # irregular sampling rate
                channel_format="float32",
                source_id=f"{participant_str}pps_experiment_{timestamp}"
            )
            # Add metadata to the stream
            mouse_info.desc().append_child_value("manufacturer", "PPS_Experiment")
            channels = mouse_info.desc().append_child("channels")
            channels.append_child("channel").append_child_value("label", "time")
            channels.append_child("channel").append_child_value("label", "x")
            channels.append_child("channel").append_child_value("label", "y")
            
            # Create mouse stream
            self.lsl_mouse_stream = pylsl.StreamOutlet(mouse_info)
            print(f"LSL mouse click stream created: {mouse_info.name()}")
            
            # Stream for audio events
            audio_info = pylsl.StreamInfo(
                name=f"{participant_str}PPS_AudioEvents_{timestamp}", 
                type="Markers",
                channel_count=1,
                nominal_srate=0,
                channel_format="string",
                source_id=f"{participant_str}pps_experiment_{timestamp}"
            )
            
            # Add metadata to the stream
            audio_info.desc().append_child_value("manufacturer", "PPS_Experiment")
            
            # Create audio stream
            self.lsl_audio_stream = pylsl.StreamOutlet(audio_info)
            print(f"LSL audio event stream created: {audio_info.name()}")
            
            # Create streams for audio content - looming and tactile
            # Only do this if participant ID is known
            if update_participant_id is not None or self.participant_id is not None:
                # Use actual participant ID if available
                pid = update_participant_id if update_participant_id is not None else self.participant_id
                
                # Looming audio stream - mono
                self.lsl_looming_info = pylsl.StreamInfo(
                    name=f"{participant_str}PPS_LoomingAudio_{timestamp}",
                    type="Audio",
                    channel_count=1,  # mono audio
                    nominal_srate=48000,  # standard audio rate
                    channel_format="float32",
                    source_id=f"{participant_str}pps_experiment_looming_{timestamp}"
                )
                self.lsl_looming_info.desc().append_child_value("manufacturer", "PPS_Experiment")
                self.lsl_looming_info.desc().append_child_value("participant_id", str(pid))
                self.lsl_looming_stream = pylsl.StreamOutlet(self.lsl_looming_info)
                print(f"LSL looming audio stream created: {self.lsl_looming_info.name()}")
                
                # Tactile audio stream - mono
                self.lsl_tactile_info = pylsl.StreamInfo(
                    name=f"{participant_str}PPS_TactileAudio_{timestamp}",
                    type="Audio",
                    channel_count=1,  # mono audio
                    nominal_srate=48000,  # standard audio rate
                    channel_format="float32",
                    source_id=f"{participant_str}pps_experiment_tactile_{timestamp}"
                )
                self.lsl_tactile_info.desc().append_child_value("manufacturer", "PPS_Experiment")
                self.lsl_tactile_info.desc().append_child_value("participant_id", str(pid))
                self.lsl_tactile_stream = pylsl.StreamOutlet(self.lsl_tactile_info)
                print(f"LSL tactile audio stream created: {self.lsl_tactile_info.name()}")
            
        except Exception as e:
            print(f"Error initializing LSL streams: {e}")
            traceback.print_exc()
            # Disable LSL
            global LSL_AVAILABLE
            LSL_AVAILABLE = False
    
    def send_lsl_mouse_marker(self, time_offset, x, y):
        """Send mouse click event to LSL stream."""
        if LSL_AVAILABLE and self.lsl_mouse_stream:
            try:
                # Send time, x, y as a vector
                self.lsl_mouse_stream.push_sample([time_offset, float(x), float(y)])
                print(f"LSL mouse marker sent: time={time_offset:.3f}, x={x}, y={y}")
            except Exception as e:
                print(f"Error sending LSL mouse marker: {e}")
    
    def send_lsl_audio_marker(self, marker_text):
        """Send audio event to LSL stream."""
        if LSL_AVAILABLE and self.lsl_audio_stream:
            try:
                # Include participant ID and timestamp in the marker
                full_marker = f"P{self.participant_id}_{marker_text}_{datetime.datetime.now().isoformat()}"
                self.lsl_audio_stream.push_sample([full_marker])
                print(f"LSL audio marker sent: {marker_text}")
            except Exception as e:
                print(f"Error sending LSL audio marker: {e}")
    
    def stream_audio_to_lsl(self, looming_data, tactile_data, sample_rate, start_offset_seconds=0):
        """
        Stream audio data to LSL outlets (limited to 2 minutes).
        
        Args:
            looming_data: Numpy array with looming audio data
            tactile_data: Numpy array with tactile audio data
            sample_rate: Sample rate of both audio streams
            start_offset_seconds: Offset to start streaming from
        """
        if not LSL_AVAILABLE or not hasattr(self, 'lsl_looming_stream') or not hasattr(self, 'lsl_tactile_stream'):
            print("LSL audio streaming not available - streams not initialized")
            return
            
        try:
            print("Starting audio data streaming to LSL (2 minutes max)")
            
            # Calculate offset in samples
            offset_samples = int(start_offset_seconds * sample_rate)
            
            # Calculate how many samples for 2 minutes
            two_minutes_samples = int(sample_rate * 60 * 2)
            
            # Create slices with offset applied
            if offset_samples >= len(looming_data):
                print(f"Warning: Offset {start_offset_seconds}s exceeds looming data length")
                return
                
            # Apply offset
            looming_slice = looming_data[offset_samples:]
            tactile_slice = tactile_data[offset_samples:]
            
            # Limit to 2 minutes
            if len(looming_slice) > two_minutes_samples:
                looming_slice = looming_slice[:two_minutes_samples]
            if len(tactile_slice) > two_minutes_samples:
                tactile_slice = tactile_slice[:two_minutes_samples]
                
            # Make sure both slices are the same length
            min_length = min(len(looming_slice), len(tactile_slice))
            looming_slice = looming_slice[:min_length]
            tactile_slice = tactile_slice[:min_length]
            
            print(f"Streaming {min_length / sample_rate:.2f} seconds of audio data to LSL")
            
            # Convert to mono if needed
            if len(looming_slice.shape) > 1 and looming_slice.shape[1] > 1:
                looming_slice = np.mean(looming_slice, axis=1)
            if len(tactile_slice.shape) > 1 and tactile_slice.shape[1] > 1:
                tactile_slice = np.mean(tactile_slice, axis=1)
            
            # Start streaming thread
            threading.Thread(
                target=self._stream_audio_chunks,
                args=(looming_slice, tactile_slice, sample_rate),
                daemon=True
            ).start()
            
        except Exception as e:
            print(f"Error starting LSL audio streaming: {e}")
            traceback.print_exc()
    
    def _stream_audio_chunks(self, looming_data, tactile_data, sample_rate):
        """
        Internal helper to stream audio data in chunks.
        
        Args:
            looming_data: Numpy array with looming audio data (mono)
            tactile_data: Numpy array with tactile audio data (mono)
            sample_rate: Sample rate of both audio streams
        """
        try:
            # Process in chunks for efficiency
            chunk_size = 512  # Smaller chunks for lower latency
            total_chunks = len(looming_data) // chunk_size
            
            # Send marker that streaming is starting
            if LSL_AVAILABLE and self.lsl_audio_stream:
                self.send_lsl_audio_marker("audio_streaming_started")
            
            # Stream chunks
            for i in range(0, len(looming_data), chunk_size):
                # Exit if experiment stopped
                if self.stop_audio:
                    print("LSL audio streaming stopped due to experiment end")
                    break
                    
                # Get chunks
                looming_chunk = looming_data[i:i+chunk_size]
                tactile_chunk = tactile_data[i:i+chunk_size]
                
                # Make sure chunks are the same length
                min_chunk_length = min(len(looming_chunk), len(tactile_chunk))
                looming_chunk = looming_chunk[:min_chunk_length]
                tactile_chunk = tactile_chunk[:min_chunk_length]
                
                # Stream chunks sample by sample
                for j in range(min_chunk_length):
                    # Push to LSL streams
                    self.lsl_looming_stream.push_sample([float(looming_chunk[j])])
                    self.lsl_tactile_stream.push_sample([float(tactile_chunk[j])])
                
                # Print progress periodically (every ~10% or 1000 chunks)
                if i % (chunk_size * 1000) == 0 or i == 0:
                    progress = (i // chunk_size) / total_chunks * 100 if total_chunks > 0 else 100
                    elapsed_time = i / sample_rate
                    print(f"LSL streaming progress: {progress:.1f}% ({elapsed_time:.1f}s)")
                
                # Sleep a small amount to reduce CPU usage but maintain timing
                # This creates a slight delay but prevents overrunning the LSL buffer
                time.sleep(chunk_size / (sample_rate * 4))  # Sleep 1/4 of the chunk duration
            
            print("LSL audio streaming completed")
            
            # Send marker that streaming is complete
            if LSL_AVAILABLE and self.lsl_audio_stream:
                self.send_lsl_audio_marker("audio_streaming_completed")
                
        except Exception as e:
            print(f"Error during LSL audio streaming: {e}")
            traceback.print_exc()

    def ensure_participant_results_dir(self):
        """
        Create and return the participant-specific results directory.
        """
        if self.participant_id is None:
            return None
            
        # Format participant ID with leading zeros (e.g., participant_01)
        participant_dir = f"participant_{self.participant_id:02d}"
        
        # Create the full path
        participant_results_dir = os.path.join(RESULTS_DIR, participant_dir)
        os.makedirs(participant_results_dir, exist_ok=True)
        
        self.participant_results_dir = participant_results_dir
        print(f"Participant results directory: {participant_results_dir}")
        
        return participant_results_dir

    def scan_available_participants(self):
        """Scan for available participants based on design files."""
        self.available_participants = []
        
        # Look for participant design files
        pattern = os.path.join(EXPERIMENT_LOG_DIR, "participant_*_design.csv")
        
        for file_path in glob.glob(pattern):
            # Extract participant ID from filename
            match = re.search(r'participant_(\d+)_design\.csv', os.path.basename(file_path))
            if match:
                participant_id = int(match.group(1))
                
                # Check if corresponding audio files exist
                looming_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{participant_id}_design_looming.wav")
                tactile_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{participant_id}_design_tactile.wav")
                
                if os.path.exists(looming_file) and os.path.exists(tactile_file):
                    self.available_participants.append(participant_id)
        
        # Sort numerically
        self.available_participants.sort()
        
        print(f"Found {len(self.available_participants)} available participants")

    def format_time_mmsss(self, time_seconds):
        """
        Format a time value in seconds to MM:SS.S format.
        
        Args:
            time_seconds: Time in seconds
            
        Returns:
            String: Formatted time string in MM:SS.S format (e.g., "01:37.6")
        """
        if time_seconds is None:
            return None
            
        minutes = int(time_seconds // 60)
        seconds = time_seconds % 60
        return f"{minutes:02d}:{seconds:.1f}"

    def validate_start_time(self, P):
        """
        Validate start time entry - only allow numbers and one decimal point.
        For use with tkinter validation.
        """
        if P == "":
            return True  # Allow empty field
            
        # Allow numbers and up to one decimal point
        if re.match(r"^\d+(\.\d*)?$", P) is not None:
            # Check if value is reasonable (less than 30 minutes)
            try:
                value = float(P)
                if value < 30:  # Assuming max 30 minutes
                    return True
            except:
                return False
        return False
    
    def create_gui(self):
        """Create the GUI with optimized layout using grid and proper scaling."""
        self.root = tk.Tk()
        self.root.title("PPS Experiment Runner")
        
        # Set protocol for window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Set window size to 85% of screen
        window_width = int(screen_width * 0.85)
        window_height = int(screen_height * 0.85)
        self.root.geometry(f"{window_width}x{window_height}")
        
        # Configure grid to be responsive
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Main frame with padding
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.grid(row=0, column=0, sticky="nsew")
        
        # Configure main_frame's grid for the new layout
        main_frame.columnconfigure(0, weight=2)  # Left column (controls)
        main_frame.columnconfigure(1, weight=1)  # Right column (for click area)
        main_frame.rowconfigure(0, weight=0)  # Title row - fixed height
        main_frame.rowconfigure(1, weight=3)  # Main content - expandable
        main_frame.rowconfigure(2, weight=2)  # Timeline row - expandable
        
        # Title - spans both columns
        title_frame = ttk.Frame(main_frame)
        title_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(title_frame, text="PPS Experiment Runner", font=("Arial", 16, "bold")).pack(pady=5)
        
        # ===== LEFT SIDE =====
        # Create left side container frame for controls and status
        left_frame = ttk.Frame(main_frame)
        left_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        left_frame.columnconfigure(0, weight=1)
        
        # Control panel on the left
        control_panel = ttk.Frame(left_frame)
        control_panel.grid(row=0, column=0, sticky="ew")
        
        # Top control area - combines experiment control and participant selection in a single area
        control_frame = ttk.LabelFrame(control_panel, text="Experiment Control", padding=10)
        control_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        control_frame.columnconfigure(0, weight=1)
        
        # Create a container for controls with proper layout
        controls_container = ttk.Frame(control_frame)
        controls_container.grid(row=0, column=0, sticky="ew")
        controls_container.columnconfigure(0, weight=0)  # Fixed width
        controls_container.columnconfigure(1, weight=1)  # Expands
        
        # Top row: Participant selection
        participant_label = ttk.Label(controls_container, text="Participant ID:")
        participant_label.grid(row=0, column=0, sticky="w", padx=(0, 5), pady=5)
        
        self.participant_var = tk.StringVar()
        if self.available_participants:
            self.participant_var.set(str(self.available_participants[0]))
        
        selection_frame = ttk.Frame(controls_container)
        selection_frame.grid(row=0, column=1, sticky="w")
        
        participant_dropdown = ttk.Combobox(
            selection_frame, 
            textvariable=self.participant_var,
            values=[str(p) for p in self.available_participants],
            width=5
        )
        participant_dropdown.pack(side=tk.LEFT, padx=(0, 10))
        
        # Refresh button
        ttk.Button(
            selection_frame, 
            text="Refresh List",
            command=self.refresh_participants
        ).pack(side=tk.LEFT)
        
        # Middle row: Start time entry
        # Register validation command
        vcmd = (self.root.register(self.validate_start_time), '%P')
        
        start_label = ttk.Label(controls_container, text="Start at minute:")
        start_label.grid(row=1, column=0, sticky="w", padx=(0, 5), pady=5)
        
        self.start_time_var = tk.StringVar(value="0.0")
        self.start_time_entry = ttk.Entry(
            controls_container, 
            textvariable=self.start_time_var,
            width=8,
            validate="key",
            validatecommand=vcmd
        )
        self.start_time_entry.grid(row=1, column=1, sticky="w", pady=5)

        # Audio device display row
        device_label = ttk.Label(controls_container, text="Audio devices:")
        device_label.grid(row=2, column=0, sticky="w", padx=(0, 5), pady=5)
        
        self.device_info_var = tk.StringVar()
        looming_name = f"Default" if self.looming_device_id is None else f"ID {self.looming_device_id}"
        tactile_name = f"Default" if self.tactile_device_id is None else f"ID {self.tactile_device_id}"
        self.device_info_var.set(f"Looming: {looming_name}, Tactile: {tactile_name}")
        
        ttk.Label(
            controls_container,
            textvariable=self.device_info_var,
            font=("Arial", 9)
        ).grid(row=2, column=1, sticky="w", pady=5)
        
        # Button row
        button_frame = ttk.Frame(controls_container)
        button_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=5)
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        
        # Start button
        self.start_button = ttk.Button(
            button_frame, text="START EXPERIMENT",
            command=self.start_experiment
        )
        self.start_button.grid(row=0, column=0, padx=5, sticky="ew")
        
        # Stop button
        self.stop_button = ttk.Button(
            button_frame, text="STOP",
            command=self.stop_experiment,
            state=tk.DISABLED
        )
        self.stop_button.grid(row=0, column=1, padx=5, sticky="ew")
        
        # Quit button
        ttk.Button(
            button_frame, text="QUIT",
            command=self.on_close
        ).grid(row=0, column=2, padx=5, sticky="ew")
        
        # Status display
        status_frame = ttk.LabelFrame(left_frame, text="Status", padding=10)
        status_frame.grid(row=1, column=0, sticky="ew", pady=5)
        status_frame.columnconfigure(0, weight=1)
        
        self.status_var = tk.StringVar(value="Select a participant and press START EXPERIMENT")
        self.status_label = ttk.Label(
            status_frame, 
            textvariable=self.status_var,
            font=("Arial", 11, "bold"),
            wraplength=int(window_width * 0.25)  # Wrap text to ensure it fits
        )
        self.status_label.grid(row=0, column=0, sticky="w", pady=5)
        
        # Side-by-side response and recovery frames
        feedback_frame = ttk.Frame(left_frame)
        feedback_frame.grid(row=2, column=0, sticky="ew", pady=5)
        feedback_frame.columnconfigure(0, weight=1)
        feedback_frame.columnconfigure(1, weight=1)
        
        # Response display - left side
        self.response_frame = ttk.LabelFrame(feedback_frame, text="Latest Response", padding=10)
        self.response_frame.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        
        self.response_var = tk.StringVar(value="No responses yet")
        self.response_label = ttk.Label(
            self.response_frame, 
            textvariable=self.response_var, 
            font=("Arial", 10),
            wraplength=int(window_width * 0.15)  # Wrap text appropriately
        )
        self.response_label.pack(pady=2, fill=tk.X)
        
        # Recovery status - right side
        self.recovery_frame = ttk.LabelFrame(feedback_frame, text="Recovery Status", padding=10)
        self.recovery_frame.grid(row=0, column=1, sticky="ew")
        
        self.recovery_var = tk.StringVar(value="Recovery not started")
        self.recovery_label = ttk.Label(
            self.recovery_frame,
            textvariable=self.recovery_var,
            font=("Arial", 10),
            wraplength=int(window_width * 0.15)  # Wrap text appropriately
        )
        self.recovery_label.pack(pady=2, fill=tk.X)
        
        # Progress bar
        progress_frame = ttk.Frame(left_frame)
        progress_frame.grid(row=3, column=0, sticky="ew", pady=5)
        progress_frame.columnconfigure(0, weight=1)
        
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            progress_frame, 
            orient="horizontal",
            mode="determinate",
            variable=self.progress_var
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        
        # ===== RIGHT SIDE (Click Area) - Now smaller =====
        # Mouse click visualization area - takes up top right quadrant
        click_frame = ttk.LabelFrame(main_frame, text="Mouse Click Area", padding=10)
        click_frame.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        click_frame.rowconfigure(0, weight=1)
        click_frame.columnconfigure(0, weight=1)
        
        # Canvas for visualizing mouse clicks - reduced height
        self.click_canvas = tk.Canvas(click_frame, bg="lightyellow")
        self.click_canvas.grid(row=0, column=0, sticky="nsew", pady=5, padx=5)
        
        # Add text to the click area - will be positioned by update_click_canvas
        self.click_text = self.click_canvas.create_text(
            window_width//4, 50,
            text="CLICK HERE WHEN YOU HEAR\nTHE TACTILE STIMULUS",
            font=("Arial", 14, "bold"), fill="blue",
            tags=["click_text"]
        )
        
        # Add click counter
        self.click_counter_text = self.click_canvas.create_text(
            100, 20,
            text="Clicks: 0",
            font=("Arial", 12), fill="black",
            tags=["click_counter"]
        )
        
        # Add the binding AFTER creating the canvas
        self.click_canvas.bind("<Button-1>", self.on_mouse_click)
        
        # ===== BOTTOM TIMELINE AREA (FULL WIDTH) =====
        # Timeline frame spans both columns
        timeline_frame = ttk.LabelFrame(main_frame, text="Experiment Timeline", padding=10)
        timeline_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=5)
        timeline_frame.columnconfigure(0, weight=1)
        
        # First half timeline
        timeline_label1 = ttk.Label(timeline_frame, text="First Half:")
        timeline_label1.grid(row=0, column=0, sticky="w", pady=(0, 2))
        
        self.timeline_canvas1 = tk.Canvas(timeline_frame, height=50, bg="white")
        self.timeline_canvas1.grid(row=1, column=0, sticky="ew", pady=2)
        
        # Second half timeline
        timeline_label2 = ttk.Label(timeline_frame, text="Second Half:")
        timeline_label2.grid(row=2, column=0, sticky="w", pady=(5, 2))
        
        self.timeline_canvas2 = tk.Canvas(timeline_frame, height=50, bg="white")
        self.timeline_canvas2.grid(row=3, column=0, sticky="ew", pady=2)
        
        # Initialize timeline parameters
        timeline_start_x = 50
        self.timeline_end_x = window_width - 120  # More space for full-width timeline
        
        # First timeline
        self.timeline_y1 = 25
        self.timeline_canvas1.create_line(
            timeline_start_x, self.timeline_y1, 
            self.timeline_end_x, self.timeline_y1, 
            width=2, tags=["timeline1"]
        )
        self.timeline_canvas1.create_text(
            timeline_start_x - 40, self.timeline_y1, 
            text="0:00", font=("Arial", 8), 
            tags=["timeline_text"]
        )
        
        # First timeline progress indicator
        self.progress_line1 = self.timeline_canvas1.create_line(
            timeline_start_x, self.timeline_y1 - 15, 
            timeline_start_x, self.timeline_y1 + 15, 
            width=3, fill="green", state="hidden", 
            tags=["progress_line"]
        )
        
        # Second timeline
        self.timeline_y2 = 25
        self.timeline_canvas2.create_line(
            timeline_start_x, self.timeline_y2, 
            self.timeline_end_x, self.timeline_y2, 
            width=2, tags=["timeline2"]
        )
        
        # Second timeline progress indicator
        self.progress_line2 = self.timeline_canvas2.create_line(
            timeline_start_x, self.timeline_y2 - 15, 
            timeline_start_x, self.timeline_y2 + 15, 
            width=3, fill="green", state="hidden", 
            tags=["progress_line"]
        )
        
        # Initialize markers list for each timeline
        self.timeline_markers1 = []
        self.timeline_markers2 = []
        
        # Bind window resize events
        self.root.bind("<Configure>", self.on_window_resize)
        
        # Initial update of canvas positions
        self.root.update_idletasks()
        self.update_click_canvas(window_width, window_height)
        self.update_timeline_canvases()

    def on_window_resize(self, event):
        """Handle window resize events."""
        # Only respond to the root window's resize events
        if event.widget == self.root:
            # Get new window dimensions
            window_width = event.width
            window_height = event.height
            
            # Update canvases
            self.update_click_canvas(window_width, window_height)
            self.update_timeline_canvases()
        
    def update_click_canvas(self, window_width, window_height):
        """Update the click canvas layout on resize."""
        # Get current canvas dimensions
        canvas_width = self.click_canvas.winfo_width()
        canvas_height = self.click_canvas.winfo_height()
        
        # Don't update if canvas is too small (not yet fully created)
        if canvas_width < 50 or canvas_height < 50:
            return
            
        # Update the center text position
        self.click_canvas.coords(
            self.click_text,
            canvas_width // 2,
            canvas_height // 2
        )
        
        # Update click counter position (top left with margin)
        self.click_canvas.coords(
            self.click_counter_text,
            50,  # Left margin
            20   # Top margin
        )
        
    def update_timeline_canvases(self):
        """Update timeline canvases on resize."""
        # Update first timeline
        canvas1_width = self.timeline_canvas1.winfo_width()
        canvas2_width = self.timeline_canvas2.winfo_width()
        
        if canvas1_width < 50 or canvas2_width < 50:  # Not yet fully created
            return
            
        timeline_start_x = 50
        self.timeline_end_x = max(canvas1_width, canvas2_width) - 60
        
        # Update main timeline lines
        self.timeline_canvas1.coords(
            "timeline1",
            timeline_start_x, self.timeline_y1,
            self.timeline_end_x, self.timeline_y1
        )
        
        self.timeline_canvas2.coords(
            "timeline2",
            timeline_start_x, self.timeline_y2,
            self.timeline_end_x, self.timeline_y2
        )

    def refresh_participants(self):
        """Refresh the list of available participants."""
        previous_selection = self.participant_var.get()
        
        # Scan for available participants
        self.scan_available_participants()
        
        # Update dropdown values
        participant_dropdown = self.root.nametowidget(
            self.participant_var.winfo_pathname(self.participant_var.winfo_id())
        )
        participant_dropdown['values'] = [str(p) for p in self.available_participants]
        
        # Try to keep previous selection if it still exists
        if previous_selection in [str(p) for p in self.available_participants]:
            self.participant_var.set(previous_selection)
        elif self.available_participants:
            self.participant_var.set(str(self.available_participants[0]))
        else:
            self.participant_var.set("")

    def on_close(self):
        """Handle window close event."""
        if self.experiment_running:
            if messagebox.askyesno("Quit", "Experiment is running. Are you sure you want to quit?"):
                # Stop audio playback when closing the window
                self.stop_audio = True
                sd.stop()
                print("Audio playback stopped")
                
                # Save any pending data
                self.finalize_results_csv()
                
                self.root.destroy()
        else:
            self.root.destroy()

    def on_mouse_click(self, event):
        """
        Handle mouse click events during the experiment.
        Calculate reaction time and update the results CSV.
        """
        if not self.experiment_running or self.start_time is None:
            return
            
        # Calculate time since experiment start
        current_time = time.perf_counter() - self.start_time
        
        # Add start offset to get actual time in the full audio
        actual_audio_time = current_time + (self.start_offset_minutes * 60)
        
        # Add to mouse clicks list
        click_data = {
            "time": current_time,
            "audio_time": actual_audio_time,
            "timestamp": datetime.datetime.now().isoformat(),
            "x": event.x,
            "y": event.y,
            "recovery_phase": self.recovery_phase
        }
        self.mouse_clicks.append(click_data)
        
        # Send LSL marker for the mouse click
        if LSL_AVAILABLE:
            self.send_lsl_mouse_marker(current_time, event.x, event.y)
        
        # Update click count display
        self.click_count += 1
        self.click_canvas.itemconfig(
            self.click_counter_text, 
            text=f"Clicks: {self.click_count}"
        )
        
        # Create a visual indication of the click
        click_x, click_y = event.x, event.y
        circle = self.click_canvas.create_oval(
            click_x-10, click_y-10, click_x+10, click_y+10, 
            fill="red", outline="black"
        )
        
        # Fade out the circle after a short time
        self.root.after(500, lambda c=circle: self.click_canvas.delete(c))
        
        # Add click marker to timeline - use current_time for display purposes
        self.add_timeline_marker(current_time, "red")
        
        print(f"Mouse click at {current_time:.3f} seconds (actual audio time: {actual_audio_time:.3f}s, recovery: {self.recovery_phase})")
        
        # Process click for reaction time calculation - use actual_audio_time for matching with tactile times
        self.process_click_reaction_time(actual_audio_time, click_data)

    def play_audio_files_synchronized(self, looming_file, tactile_file, start_offset_seconds=0, is_recovery=False):
        """
        Play audio files using sounddevice, starting from the specified offset with perfect synchronization.
        Uses the previously detected audio devices for routing stimuli to appropriate outputs.
        
        Args:
            looming_file: Path to looming audio file
            tactile_file: Path to tactile audio file
            start_offset_seconds: Offset in seconds to start playback from
            is_recovery: Whether this is recovery phase audio
            
        Returns:
            True if completed successfully, False if stopped or error occurred
        """
        try:
            print(f"Loading audio files for {'recovery' if is_recovery else 'main'} phase:")
            print(f"- Looming: {looming_file}")
            print(f"- Tactile: {tactile_file}")
            
            # Load complete audio data
            looming_data, looming_sr = sf.read(looming_file)
            tactile_data, tactile_sr = sf.read(tactile_file)
            
            print(f"Audio loaded - Looming: {looming_data.shape}, Tactile: {tactile_data.shape}")
            
            # Start LSL streaming of audio data (limited to 2 minutes, in a separate thread)
            if LSL_AVAILABLE and hasattr(self, 'lsl_looming_stream') and hasattr(self, 'lsl_tactile_stream'):
                self.stream_audio_to_lsl(
                    looming_data, 
                    tactile_data, 
                    looming_sr,
                    start_offset_seconds
                )
            
            # Verify sample rates match
            if looming_sr != tactile_sr:
                print(f"ERROR: Sample rate mismatch - Looming: {looming_sr}, Tactile: {tactile_sr}")
                return False
            
            # Calculate offset in samples
            if not is_recovery and start_offset_seconds > 0:
                offset_samples = int(start_offset_seconds * looming_sr)
                
                # Make sure we don't go past the end of the audio
                if offset_samples >= len(looming_data):
                    self.status_var.set(f"Error: Start offset ({start_offset_seconds/60:.1f} min) is beyond audio length")
                    print(f"ERROR: Start offset ({start_offset_seconds/60:.1f} min) exceeds audio length")
                    return False
                
                # Create slices of audio from the offset
                print(f"Starting audio from offset: {start_offset_seconds:.2f} seconds ({offset_samples} samples)")
                looming_data = looming_data[offset_samples:]
                tactile_data = tactile_data[offset_samples:]
            
            # Convert stereo to mono if needed
            if len(looming_data.shape) > 1 and looming_data.shape[1] > 1:
                print("Converting looming audio from stereo to mono for playback")
                looming_data = np.mean(looming_data, axis=1)
            if len(tactile_data.shape) > 1 and tactile_data.shape[1] > 1:
                print("Converting tactile audio from stereo to mono for playback")
                tactile_data = np.mean(tactile_data, axis=1)
            
            # If this is a recovery phase, announce it
            if is_recovery:
                self.root.after(0, lambda: self.recovery_var.set("Starting recovery phase"))
                self.root.after(0, lambda: self.status_var.set("Playing missed trials - continue responding"))
                self.recovery_phase = True
                
                # Send LSL marker for recovery start
                if LSL_AVAILABLE:
                    self.send_lsl_audio_marker("recovery_phase_start")
            
            # Create a thread to monitor upcoming tactile events
            def tactile_monitor_thread():
                # Monitor for upcoming tactile stimuli and prepare GUI with appropriate lead time
                actual_offset = start_offset_seconds if not is_recovery else 0
                offset_samples = int(actual_offset * looming_sr)
                
                try:
                    position = 0  # Current position in samples
                    while not self.stop_audio and position < len(looming_data):
                        # Calculate current time in the audio (second)
                        current_time = actual_offset + (position / looming_sr)
                        
                        # Check for upcoming tactile events and prep GUI
                        self.process_upcoming_tactile(current_time)
                        
                        # Sleep a short time to reduce CPU usage
                        time.sleep(0.05)  # 50ms check interval
                        
                        # Update position (approximate based on time passed)
                        position = int((time.perf_counter() - self.start_time) * looming_sr)
                except Exception as e:
                    print(f"Error in tactile monitor thread: {e}")
                    traceback.print_exc()
            
            # SYNCHRONIZED PLAYBACK APPROACH WITH DEVICE SELECTION
            # Create synchronized audio streams using callback method
            try:
                # Set up a shared state for both streams
                stop_event = threading.Event()
                audio_done = threading.Event()
                
                # Calculate remaining duration in seconds for progress updates
                remaining_duration = len(looming_data) / looming_sr
                print(f"Remaining audio duration: {remaining_duration:.2f} seconds")
                
                # Start tactile monitor thread
                monitor_thread = threading.Thread(target=tactile_monitor_thread, daemon=True)
                monitor_thread.start()
                
                # Create separate device streams but start them together
                looming_stream = None
                tactile_stream = None
                
                # Create callback functions for both streams
                def looming_callback(outdata, frames, time, status):
                    if stop_event.is_set():
                        raise sd.CallbackStop
                    
                    remaining = len(looming_data) - looming_pos[0]
                    if remaining < frames:
                        outdata[:remaining] = looming_data[looming_pos[0]:].reshape(-1, 1)
                        outdata[remaining:] = 0
                        looming_pos[0] += remaining
                        raise sd.CallbackStop
                    else:
                        outdata[:] = looming_data[looming_pos[0]:looming_pos[0] + frames].reshape(-1, 1)
                        looming_pos[0] += frames
                
                def tactile_callback(outdata, frames, time, status):
                    if stop_event.is_set():
                        raise sd.CallbackStop
                    
                    remaining = len(tactile_data) - tactile_pos[0]
                    if remaining < frames:
                        outdata[:remaining] = tactile_data[tactile_pos[0]:].reshape(-1, 1)
                        outdata[remaining:] = 0
                        tactile_pos[0] += remaining
                        raise sd.CallbackStop
                    else:
                        outdata[:] = tactile_data[tactile_pos[0]:tactile_pos[0] + frames].reshape(-1, 1)
                        tactile_pos[0] += frames
                
                # Stream position trackers
                looming_pos = [0]
                tactile_pos = [0]
                
                # Start audio streams with specific devices
                try:
                    print(f"Using looming device ID: {self.looming_device_id}")
                    print(f"Using tactile device ID: {self.tactile_device_id}")
                    
                    # Create streams with device-specific routing
                    looming_stream = sd.OutputStream(
                        samplerate=looming_sr,
                        channels=1,
                        callback=looming_callback,
                        device=self.looming_device_id,  # Use detected Output 1/2 device
                        finished_callback=lambda: audio_done.set()
                    )
                    
                    tactile_stream = sd.OutputStream(
                        samplerate=tactile_sr,
                        channels=1,
                        callback=tactile_callback,
                        device=self.tactile_device_id  # Use detected Woojer device
                    )
                    
                    # Start both streams - this ensures perfect synchronization
                    print("Starting synchronized audio streams...")
                    looming_stream.start()
                    tactile_stream.start()
                    
                    # Send LSL marker for audio start
                    if LSL_AVAILABLE:
                        marker = "recovery_audio_start" if is_recovery else "main_audio_start"
                        if start_offset_seconds > 0 and not is_recovery:
                            marker += f"_offset_{start_offset_seconds:.1f}s"
                        self.send_lsl_audio_marker(marker)
                    
                    # Monitor for stop flag
                    while not audio_done.is_set() and not self.stop_audio:
                        time.sleep(0.1)
                    
                    # Set stop event if necessary
                    if self.stop_audio:
                        stop_event.set()
                        print("Audio playback stopped by user")
                        return False
                    
                    print("Audio playback completed")
                    
                    # Send LSL marker for audio completion
                    if LSL_AVAILABLE:
                        marker = "recovery_audio_complete" if is_recovery else "main_audio_complete"
                        self.send_lsl_audio_marker(marker)
                        
                    return True
                    
                except Exception as e:
                    print(f"Error with device-specific playback: {e}")
                    traceback.print_exc()
                    
                    # Fall back to default devices if specific ones fail
                    print("Falling back to default output devices")
                    
                    if looming_stream:
                        looming_stream.stop()
                        looming_stream.close()
                    
                    if tactile_stream:
                        tactile_stream.stop()
                        tactile_stream.close()
                    
                    # Try with default devices
                    looming_stream = sd.OutputStream(
                        samplerate=looming_sr,
                        channels=1,
                        callback=looming_callback,
                        finished_callback=lambda: audio_done.set()
                    )
                    tactile_stream = sd.OutputStream(
                        samplerate=tactile_sr,
                        channels=1,
                        callback=tactile_callback
                    )
                    
                    # Start both streams
                    looming_stream.start()
                    tactile_stream.start()
                    
                    # Monitor for stop flag
                    while not audio_done.is_set() and not self.stop_audio:
                        time.sleep(0.1)
                    
                    # Set stop event if necessary
                    if self.stop_audio:
                        stop_event.set()
                        print("Audio playback stopped by user")
                        return False
                    
                    print("Audio playback completed (using default devices)")
                    return True
                    
            except Exception as e:
                print(f"Error in synchronized audio playback: {e}")
                traceback.print_exc()
                return False
                
        except Exception as e:
            print(f"Error loading audio files: {e}")
            traceback.print_exc()
            return False

    def start_experiment(self):
        """Start the experiment with the selected participant."""
        try:
            # Get participant ID
            try:
                self.participant_id = int(self.participant_var.get())
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Please select a valid participant ID")
                return

            # Get start offset
            try:
                self.start_offset_minutes = float(self.start_time_var.get())
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Please enter a valid start time")
                return

            # Create participant results directory
            self.ensure_participant_results_dir()
            if not self.participant_results_dir:
                messagebox.showerror("Error", "Could not create results directory")
                return

            # Load design file
            design_file = os.path.join(EXPERIMENT_LOG_DIR, f"participant_{self.participant_id:02d}_design.csv")
            if not os.path.exists(design_file):
                messagebox.showerror("Error", f"Design file not found: {design_file}")
                return

            self.design_df = pd.read_csv(design_file)
            
            # Get audio files
            looming_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{self.participant_id:02d}_design_looming.wav")
            tactile_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{self.participant_id:02d}_design_tactile.wav")

            if not os.path.exists(looming_file) or not os.path.exists(tactile_file):
                messagebox.showerror("Error", "Audio files not found")
                return

            # Extract tactile times from design file
            self.tactile_times = self.design_df['tactile_time'].tolist()

            # Initialize results DataFrame
            self.results_df = pd.DataFrame(columns=[
                'participant_id', 'trial_number', 'tactile_time', 'click_time',
                'reaction_time', 'click_x', 'click_y', 'recovery_phase',
                'timestamp'
            ])

            # Create results file
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.results_file = os.path.join(
                self.participant_results_dir,
                f"participant_{self.participant_id:02d}_results_{timestamp}.csv"
            )

            # Update GUI state
            self.experiment_running = True
            self.start_time = time.perf_counter()
            self.audio_start_time = self.start_time
            self.click_count = 0
            self.mouse_clicks = []
            self.timeline_markers1 = []
            self.timeline_markers2 = []
            self.next_tactile_processed.clear()

            # Update button states
            self.start_button.state(['disabled'])
            self.stop_button.state(['!disabled'])
            self.participant_var.set("")
            self.start_time_var.set("0.0")

            # Update status
            self.status_var.set("Experiment running...")
            self.response_var.set("Waiting for responses...")
            self.recovery_var.set("Main phase")

            # Start audio playback
            success = self.play_audio_files_synchronized(
                looming_file,
                tactile_file,
                start_offset_seconds=self.start_offset_minutes * 60
            )

            if not success:
                self.stop_experiment()
                messagebox.showerror("Error", "Failed to start audio playback")
                return

            # Initialize LSL streams with participant ID
            if LSL_AVAILABLE:
                self.initialize_lsl_streams(update_participant_id=self.participant_id)

        except Exception as e:
            print(f"Error starting experiment: {e}")
            traceback.print_exc()
            messagebox.showerror("Error", f"Failed to start experiment: {str(e)}")

    def stop_experiment(self):
        """Stop the experiment and save results."""
        try:
            # Stop audio playback
            self.stop_audio = True
            sd.stop()
            print("Audio playback stopped")

            # Update GUI state
            self.experiment_running = False
            self.start_button.state(['!disabled'])
            self.stop_button.state(['disabled'])
            self.status_var.set("Experiment stopped")

            # Save results
            self.finalize_results_csv()

            # Reset variables
            self.start_time = None
            self.audio_start_time = None
            self.click_count = 0
            self.mouse_clicks = []
            self.timeline_markers1 = []
            self.timeline_markers2 = []
            self.next_tactile_processed.clear()

            # Update status
            self.response_var.set("No responses yet")
            self.recovery_var.set("Recovery not started")

            print("Experiment stopped and results saved")

        except Exception as e:
            print(f"Error stopping experiment: {e}")
            traceback.print_exc()
            messagebox.showerror("Error", f"Failed to stop experiment: {str(e)}")

    def finalize_results_csv(self):
        """Save the results to CSV file."""
        if self.results_df is not None and not self.results_df.empty:
            try:
                self.results_df.to_csv(self.results_file, index=False)
                print(f"Results saved to: {self.results_file}")
            except Exception as e:
                print(f"Error saving results: {e}")
                traceback.print_exc()

def check_dependencies():
    """Check for required dependencies and print installation instructions if missing."""
    missing_deps = []
    
    # Check for pyautogui (mouse control)
    if not MOUSE_CONTROL_AVAILABLE:
        missing_deps.append("pyautogui")
    
    # Check for pylsl (LSL streaming)
    if not LSL_AVAILABLE:
        missing_deps.append("pylsl")
    
    # Check for pywin32 on Windows (window management)
    if platform.system() == 'Windows' and not WINDOWS_GUI_AVAILABLE:
        missing_deps.append("pywin32")
    
    # Print installation instructions if any dependencies are missing
    if missing_deps:
        print("\n" + "="*50)
        print("MISSING DEPENDENCIES")
        print("="*50)
        print("The following packages are missing and should be installed for full functionality:")
        
        for dep in missing_deps:
            print(f"  - {dep}")
        
        print("\nTo install all missing dependencies, run:")
        print(f"  pip install {' '.join(missing_deps)}")
        print("\nNote: The program will still run but with limited functionality.")
        print("="*50 + "\n")
        
        # Pause to make sure user sees the message
        time.sleep(2)
        
        return False
    
    return True

def main():
    """Main entry point for the application."""
    # Check for dependencies
    check_dependencies()
    
    # Create and run the application
    app = EnhancedExperimentRunner()
    app.root.mainloop()

if __name__ == "__main__":
    main()