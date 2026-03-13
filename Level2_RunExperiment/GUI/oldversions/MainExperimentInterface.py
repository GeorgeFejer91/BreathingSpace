#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Breathing Space Audio Player with Timeline

Combines audio playback capabilities with experiment timeline visualization.
Auto-detects participant information from metadata JSON files.

Features:
- Automatic participant selection from metadata
- High-quality audio playback with ASIO device support
- Timeline visualization
- Mouse click tracking
"""

import os
import time
import tkinter as tk
from tkinter import ttk, messagebox
import sounddevice as sd
import soundfile as sf
import numpy as np
import threading
import datetime
import re
import glob
import traceback
import csv
import json
from pathlib import Path

# Optional LSL integration
try:
    import pylsl
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    print("LSL not available. Install pylsl package for LSL support.")

# Configuration
BASE_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace"
EXPERIMENT_AUDIO_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentAudio_2ch")
EXPERIMENT_LOG_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentLog")
RESULTS_DIR = os.path.join(BASE_DIR, "Level2_RunExperiment", "Results")
METADATA_DIR = os.path.join(RESULTS_DIR, "participant_metadata")

# Ensure results directory exists
os.makedirs(RESULTS_DIR, exist_ok=True)

# Configure sounddevice for low latency
sd.default.latency = 'low'

def find_latest_participant_metadata():
    """Find the most recent JSON metadata file and return participant info."""
    if not os.path.exists(METADATA_DIR):
        print(f"Metadata directory does not exist: {METADATA_DIR}")
        return None
    
    # Get all JSON files in the directory
    json_files = glob.glob(os.path.join(METADATA_DIR, "*.json"))
    
    if not json_files:
        print("No JSON files found in metadata directory")
        return None
    
    # Find the most recent file by modification time
    latest_file = max(json_files, key=os.path.getmtime)
    
    try:
        with open(latest_file, 'r') as f:
            metadata = json.load(f)
        
        # Extract participant ID (assuming format like "P001")
        if 'participant_id' in metadata:
            participant_id = metadata['participant_id']
            # Extract numeric part (e.g., extract 1 from "P001")
            match = re.search(r'P0*(\d+)', participant_id)
            if match:
                participant_num = int(match.group(1))
                return {
                    'participant_id': participant_id,
                    'participant_num': participant_num,
                    'timestamp': metadata.get('timestamp', ''),
                    'file_path': latest_file
                }
    
    except Exception as e:
        print(f"Error reading metadata file: {e}")
    
    return None

def create_lsl_outlet(participant_info):
    """Create LSL outlet for participant information."""
    if not LSL_AVAILABLE:
        return None
    
    try:
        # Create stream info
        stream_info = pylsl.StreamInfo(
            name='BreathingSpaceParticipant',
            type='Markers',
            channel_count=1,
            nominal_srate=0,  # Irregular sample rate
            channel_format='string',
            source_id=f'participant_{participant_info["participant_num"]}'
        )
        
        # Add participant info to stream metadata
        desc = stream_info.desc()
        desc.append_child_value("participant_id", participant_info["participant_id"])
        desc.append_child_value("participant_num", str(participant_info["participant_num"]))
        
        # Create outlet
        outlet = pylsl.StreamOutlet(stream_info)
        print(f"Created LSL outlet for participant {participant_info['participant_id']}")
        return outlet
    
    except Exception as e:
        print(f"Error creating LSL outlet: {e}")
        return None

def debug_print(message, indent=0, text_widget=None):
    """Print debug messages with timestamps and optional indentation"""
    timestamp = time.strftime("%H:%M:%S", time.localtime())
    indent_str = "  " * indent
    log_message = f"[{timestamp}] {indent_str}{message}"
    print(log_message)
    
    # If we have a text widget, also log there
    if text_widget:
        text_widget.insert(tk.END, log_message + "\n")
        text_widget.see(tk.END)  # Auto-scroll to end

class AudioPlayer:
    def __init__(self):
        self.reset()
        
    def reset(self):
        """Reset all player state"""
        # Stop any active playback first
        self.stop_playback()
        
        # Reset all instance variables
        self.audio_data = None
        self.sample_rate = 0
        self.playback_thread = None
        self.is_playing = False
        self.is_paused = False
        self.current_position = 0
        self.output_12_id = None
        self.output_34_id = None
        self.left_stream = None
        self.right_stream = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        
        # Force stop any lingering audio
        try:
            sd.stop()
        except:
            pass

    def load_file(self, file_path, text_widget=None):
        """Load audio file and prepare for playback"""
        # First, reset the player state
        self.reset()
        
        try:
            debug_print(f"Loading file: {file_path}", 1, text_widget)
            audio, file_sr = sf.read(file_path, always_2d=True, dtype='float32')
            debug_print(f"File loaded: {len(audio)} samples, {file_sr}Hz, {audio.shape[1]} channels", 1, text_widget)
            
            self.audio_data = audio
            self.sample_rate = file_sr
            self.current_position = 0
            return True
        except Exception as e:
            debug_print(f"Error loading audio file: {e}", 1, text_widget)
            self.reset()  # Reset on error
            return False

    def prepare_playback(self, text_widget=None, buffer_size=512, test_tones=False):
        """Prepare audio devices and data for playback"""
        # Get device list
        devices = sd.query_devices()
        
        # Look for Komplete Audio ASIO devices
        self.output_12_id = None
        self.output_34_id = None
        
        for i, device in enumerate(devices):
            if device['max_output_channels'] > 0:
                # Check if it's an ASIO device
                if 'hostapi' in device and device['hostapi'] == 3:  # ASIO
                    name = device['name'].lower()
                    debug_print(f"Found ASIO device {i}: {device['name']}", 1, text_widget)
                    if "output 1/2" in name and ("komplete" in name or "1/2" in name):
                        self.output_12_id = i
                        debug_print(f"Found Output 1/2 ASIO device: ID {i} - {device['name']}", 1, text_widget)
                    elif "output 3/4" in name and ("komplete" in name or "3/4" in name):
                        self.output_34_id = i
                        debug_print(f"Found Output 3/4 ASIO device: ID {i} - {device['name']}", 1, text_widget)
        
        # Check if we found the devices
        if self.output_12_id is None or self.output_34_id is None:
            debug_print("Could not find both ASIO devices for Komplete Audio", 1, text_widget)
            debug_print("Looking for any suitable multi-channel device instead", 1, text_widget)
            
            # Try to find any multi-channel device as a fallback
            for i, device in enumerate(devices):
                if device['max_output_channels'] >= 4 and 'hostapi' in device and device['hostapi'] == 3:
                    debug_print(f"Found multi-channel ASIO device: {device['name']}", 1, text_widget)
                    self.output_12_id = i  # Use the same device for both outputs
                    self.output_34_id = i
                    break
            
            if self.output_12_id is None:
                debug_print("No suitable audio device found.", 1, text_widget)
                return False
        
        # Get device sample rate
        try:
            device_info = sd.query_devices(self.output_12_id)
            device_sr = int(device_info['default_samplerate'])
        except:
            device_sr = 44100  # Default to 44.1kHz if query fails
        
        debug_print(f"Using sample rate: {device_sr}Hz", 1, text_widget)
        debug_print(f"Using buffer size: {buffer_size} samples", 1, text_widget)
        
        # Resample if needed
        if self.sample_rate != device_sr:
            debug_print(f"Sample rate mismatch: file={self.sample_rate}Hz, device={device_sr}Hz", 1, text_widget)
            self.audio_data = simple_resample(self.audio_data, self.sample_rate, device_sr, text_widget)
            self.sample_rate = device_sr
        
        # Add test signals to beginning if requested
        if test_tones:
            test_duration = 3  # seconds
            test_samples = int(test_duration * device_sr)
            
            t = np.linspace(0, test_duration, test_samples, endpoint=False)
            test_left = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)  # 440 Hz (A4)
            test_right = 0.5 * np.sin(2 * np.pi * 880 * t).astype(np.float32)  # 880 Hz (A5)
            
            # Add fade in/out
            fade_samples = int(0.1 * device_sr)
            fade_in = np.linspace(0, 1, fade_samples)
            fade_out = np.linspace(1, 0, fade_samples)
            
            test_left[:fade_samples] *= fade_in
            test_left[-fade_samples:] *= fade_out
            test_right[:fade_samples] *= fade_in
            test_right[-fade_samples:] *= fade_out
            
            # Create a new longer audio array to PREPEND (not replace) test tones
            new_length = len(self.audio_data) + test_samples
            new_audio = np.zeros((new_length, 2), dtype=np.float32)
            
            # Place test tones at the beginning
            new_audio[:test_samples, 0] = test_left
            new_audio[:test_samples, 1] = test_right
            
            # Place original audio after the test tones
            new_audio[test_samples:, :] = self.audio_data
            
            # Replace audio data with the new combined version
            self.audio_data = new_audio
            debug_print(f"Prepended test tones: 440Hz on left, 880Hz on right ({test_duration} seconds)", 1, text_widget)
        
        return True

    def start_playback(self, text_widget=None, buffer_size=512, start_time=0.0):
        """Start audio playback from specified time position"""
        # First stop any existing playback
        if self.is_playing:
            self.stop_playback(text_widget)
            # Short delay to ensure cleanup is complete
            time.sleep(0.1)
            
        if self.audio_data is None:
            debug_print("No audio data loaded", 1, text_widget)
            return False
            
        # Calculate start position in samples
        start_sample = int(start_time * self.sample_rate)
        if start_sample >= len(self.audio_data):
            debug_print(f"Start time ({start_time}s) exceeds audio duration", 1, text_widget)
            start_sample = 0
            
        self.current_position = start_sample
        
        # Reset events
        self.stop_event.clear()
        self.pause_event.clear()
        
        # Split channels
        try:
            left_channel = self.audio_data[:, 0].copy()
            right_channel = self.audio_data[:, 1].copy()
            
            # Create stereo output for each device (we need to send stereo)
            left_output = np.column_stack((left_channel, left_channel)).astype(np.float32)
            right_output = np.column_stack((right_channel, right_channel)).astype(np.float32)
            
            # Start the playback thread
            self.playback_thread = threading.Thread(
                target=self._playback_thread_func,
                args=(left_output, right_output, buffer_size, start_sample, text_widget),
                daemon=True
            )
            
            self.is_playing = True
            self.is_paused = False
            self.playback_thread.start()
            
            debug_print(f"Playback started from position {start_time:.2f}s", 1, text_widget)
            return True
        except Exception as e:
            debug_print(f"Error starting playback: {e}", 1, text_widget)
            self.is_playing = False
            self.is_paused = False
            return False
        
    def _playback_thread_func(self, left_output, right_output, buffer_size, start_sample, text_widget):
        """Thread function for audio playback"""
        try:
            # Truncate outputs to start at the desired position
            left_output = left_output[start_sample:]
            right_output = right_output[start_sample:]
            
            # Set up callback functions for streaming
            def left_callback(outdata, frames, time, status):
                if self.stop_event.is_set():
                    raise sd.CallbackStop
                
                if self.pause_event.is_set():
                    outdata.fill(0)
                    return
                    
                position = self.current_position
                end = position + frames
                
                if end > len(left_output):
                    outdata[:len(left_output) - position] = left_output[position:]
                    outdata[len(left_output) - position:] = 0
                    raise sd.CallbackStop
                else:
                    outdata[:] = left_output[position:end]
                    self.current_position = end
            
            def right_callback(outdata, frames, time, status):
                if self.stop_event.is_set():
                    raise sd.CallbackStop
                
                if self.pause_event.is_set():
                    outdata.fill(0)
                    return
                    
                position = self.current_position
                end = position + frames
                
                if end > len(right_output):
                    outdata[:len(right_output) - position] = right_output[position:]
                    outdata[len(right_output) - position:] = 0
                    raise sd.CallbackStop
                else:
                    outdata[:] = right_output[position:end]
            
            # Start the streams
            self.left_stream = sd.OutputStream(
                device=self.output_12_id,
                channels=2,
                callback=left_callback,
                samplerate=self.sample_rate,
                blocksize=buffer_size,
                dtype='float32'
            )
            
            self.right_stream = sd.OutputStream(
                device=self.output_34_id,
                channels=2,
                callback=right_callback,
                samplerate=self.sample_rate,
                blocksize=buffer_size,
                dtype='float32'
            )
            
            self.left_stream.start()
            self.right_stream.start()
            
            # Wait for completion
            while self.left_stream.active or self.right_stream.active:
                time.sleep(0.1)
                if self.stop_event.is_set():
                    break
            
        except Exception as e:
            debug_print(f"Error in playback thread: {e}", 1, text_widget)
        
        finally:
            # Clean up
            if hasattr(self, 'left_stream') and self.left_stream:
                self.left_stream.stop()
                self.left_stream.close()
                self.left_stream = None
                
            if hasattr(self, 'right_stream') and self.right_stream:
                self.right_stream.stop()
                self.right_stream.close()
                self.right_stream = None
                
            self.is_playing = False
            self.is_paused = False
            
            # Signal in main thread that playback is complete
            if text_widget and text_widget.winfo_exists():
                text_widget.after(0, lambda: debug_print("Playback complete", 1, text_widget))
    
    def pause_playback(self, text_widget=None):
        """Pause audio playback"""
        if self.is_playing and not self.is_paused:
            self.pause_event.set()
            self.is_paused = True
            debug_print("Playback paused", 1, text_widget)
            return True
        return False
    
    def resume_playback(self, text_widget=None):
        """Resume audio playback"""
        if self.is_playing and self.is_paused:
            self.pause_event.clear()
            self.is_paused = False
            debug_print("Playback resumed", 1, text_widget)
            return True
        return False
    
    def stop_playback(self, text_widget=None):
        """Stop audio playback"""
        if not hasattr(self, 'is_playing'):
            return False
            
        # Set stop flag even if not playing to ensure clean state
        if hasattr(self, 'stop_event'):
            self.stop_event.set()
            
        # Close streams
        if hasattr(self, 'left_stream') and self.left_stream:
            try:
                self.left_stream.stop()
                self.left_stream.close()
            except Exception as e:
                if text_widget:
                    debug_print(f"Error closing left stream: {e}", 1, text_widget)
            self.left_stream = None
                
        if hasattr(self, 'right_stream') and self.right_stream:
            try:
                self.right_stream.stop()
                self.right_stream.close()
            except Exception as e:
                if text_widget:
                    debug_print(f"Error closing right stream: {e}", 1, text_widget)
            self.right_stream = None
            
        # Force stop all sounddevice streams
        try:
            sd.stop()
        except:
            pass
            
        # Wait for thread to finish
        if hasattr(self, 'playback_thread') and self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=1.0)
        
        # Reset state
        was_playing = self.is_playing
        self.is_playing = False
        self.is_paused = False
        
        if was_playing and text_widget:
            debug_print("Playback stopped", 1, text_widget)
            
        return was_playing

    def get_position(self):
        """Get current playback position in seconds"""
        if self.sample_rate > 0:
            return self.current_position / self.sample_rate
        return 0.0
    
    def get_duration(self):
        """Get audio duration in seconds"""
        if self.audio_data is not None and self.sample_rate > 0:
            return len(self.audio_data) / self.sample_rate
        return 0.0

def simple_resample(audio, orig_sr, target_sr, text_widget=None):
    """Simple resampling function using linear interpolation"""
    debug_print(f"Resampling audio from {orig_sr}Hz to {target_sr}Hz...", 1, text_widget)
    
    # Calculate the resampling ratio and new length
    ratio = target_sr / orig_sr
    new_length = int(len(audio) * ratio)
    
    # Create time arrays for interpolation
    orig_time = np.arange(len(audio)) / orig_sr
    new_time = np.arange(new_length) / target_sr
    
    # Create output array - explicitly use float32 for compatibility
    resampled = np.zeros((new_length, audio.shape[1]), dtype=np.float32)
    
    # Resample each channel separately using linear interpolation
    for channel in range(audio.shape[1]):
        resampled[:, channel] = np.interp(new_time, orig_time, audio[:, channel])
    
    debug_print(f"Resampling complete. Original samples: {len(audio)}, New samples: {len(resampled)}", 1, text_widget)
    return resampled

class BreathingSpaceApp:
    """
    Combined application with audio playback and experiment timeline
    visualization. Auto-detects participant from metadata.
    """
    def __init__(self, root):
        self.root = root
        self.root.title("Breathing Space Experiment")
        
        # Get screen dimensions for responsive layout
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = int(screen_width * 0.8)
        window_height = int(screen_height * 0.8)
        self.root.geometry(f"{window_width}x{window_height}")
        
        # Set up window close handler
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Initialize instance variables
        self.participant_info = None
        self.lsl_outlet = None
        self.experiment_running = False
        self.audio_player = AudioPlayer()
        self.mouse_clicks = []
        self.tactile_times = []
        self.timeline_markers1 = []
        self.timeline_markers2 = []
        self.click_count = 0
        self.audio_duration = 0
        self.start_time = None
        self.audio_file_path = None
        
        # Main frame
        self.main_frame = ttk.Frame(root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create GUI components
        self._create_participant_frame()
        self._create_playback_controls()
        self._create_timeline_frame(window_width)
        self._create_click_area(window_width)
        self._create_log_area()
        
        # Load participant info
        self.load_participant_from_metadata()
        
        # Set up progress updating
        self.update_progress_id = None
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = ttk.Label(root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Debug output
        debug_print("Application started", 0, self.log_text)
    
    def load_participant_from_metadata(self):
        """Load participant information from the most recent metadata file."""
        self.participant_info = find_latest_participant_metadata()
        
        if self.participant_info:
            participant_num = self.participant_info["participant_num"]
            participant_id = self.participant_info["participant_id"]
            debug_print(f"Loaded participant {participant_id} (#{participant_num}) from metadata", 0, self.log_text)
            
            # Update UI
            if hasattr(self, 'participant_label'):
                self.participant_label.config(text=f"Participant: {participant_id}")
            
            # Create LSL outlet
            if LSL_AVAILABLE:
                self.lsl_outlet = create_lsl_outlet(self.participant_info)
            
            # Look for corresponding audio file
            self.find_audio_file(participant_num)
        else:
            debug_print("No participant metadata found", 0, self.log_text)
            messagebox.showwarning(
                "No Participant", 
                "No participant metadata found. Please run the participant selector first."
            )
    
    def find_audio_file(self, participant_num):
        """Find the combined audio file for the participant."""
        audio_filename = f"participant_{participant_num}_combined.wav"
        audio_path = os.path.join(EXPERIMENT_AUDIO_DIR, audio_filename)
        
        if os.path.exists(audio_path):
            debug_print(f"Found audio file: {audio_filename}", 0, self.log_text)
            self.audio_file_path = audio_path
            
            # Update UI
            if hasattr(self, 'file_label'):
                self.file_label.config(text=f"File: {audio_filename}")
            
            # Load tactile times
            self.load_tactile_times(participant_num)
            
            # Get audio duration for timeline
            try:
                info = sf.info(audio_path)
                self.audio_duration = info.duration
                debug_print(f"Audio duration: {self.audio_duration:.2f} seconds", 0, self.log_text)
                self.update_timeline_with_duration()
            except Exception as e:
                debug_print(f"Error getting audio info: {e}", 0, self.log_text)
                
        else:
            debug_print(f"Audio file not found: {audio_path}", 0, self.log_text)
            messagebox.showwarning(
                "File Not Found", 
                f"Audio file not found: {audio_filename}\nPlease check the audio directory."
            )
            self.audio_file_path = None
    
    def _create_participant_frame(self):
        """Create the participant information area."""
        info_frame = ttk.LabelFrame(self.main_frame, text="Participant Information", padding="5")
        info_frame.pack(fill=tk.X, pady=5)
        
        # Participant info display (read-only)
        self.participant_label = ttk.Label(info_frame, text="Participant: Loading...")
        self.participant_label.grid(row=0, column=0, padx=5, pady=2, sticky=tk.W)
        
        # File info
        self.file_label = ttk.Label(info_frame, text="File: Not loaded")
        self.file_label.grid(row=1, column=0, padx=5, pady=2, sticky=tk.W)
        
        # Refresh button
        ttk.Button(
            info_frame, text="Refresh Participant",
            command=self.refresh_participant
        ).grid(row=0, column=1, rowspan=2, padx=10, pady=5, sticky=tk.E)
    
    def _create_playback_controls(self):
        """Create playback controls section"""
        controls_frame = ttk.LabelFrame(self.main_frame, text="Playback Controls", padding="5")
        controls_frame.pack(fill=tk.X, pady=5)
        
        # Time input
        time_frame = ttk.Frame(controls_frame)
        time_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=2, sticky=tk.W)
        
        ttk.Label(time_frame, text="Start Time (mm:ss):").pack(side=tk.LEFT, padx=5)
        
        self.minutes_var = tk.StringVar(value="00")
        self.seconds_var = tk.StringVar(value="00")
        
        minutes_entry = ttk.Entry(time_frame, textvariable=self.minutes_var, width=3)
        minutes_entry.pack(side=tk.LEFT)
        
        ttk.Label(time_frame, text=":").pack(side=tk.LEFT)
        
        seconds_entry = ttk.Entry(time_frame, textvariable=self.seconds_var, width=3)
        seconds_entry.pack(side=tk.LEFT, padx=(0, 10))
        
        # Progress display
        self.progress_var = tk.StringVar(value="00:00 / 00:00")
        progress_label = ttk.Label(time_frame, textvariable=self.progress_var)
        progress_label.pack(side=tk.LEFT, padx=10)
        
        # Control buttons
        button_frame = ttk.Frame(controls_frame)
        button_frame.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W+tk.E)
        
        self.play_button = ttk.Button(button_frame, text="Play", command=self.start_playback, width=8)
        self.play_button.pack(side=tk.LEFT, padx=2)
        
        self.pause_button = ttk.Button(button_frame, text="Pause", command=self.pause_resume, width=8)
        self.pause_button.pack(side=tk.LEFT, padx=2)
        self.pause_button.config(state=tk.DISABLED)
        
        self.stop_button = ttk.Button(button_frame, text="Stop", command=self.stop_playback, width=8)
        self.stop_button.pack(side=tk.LEFT, padx=2)
        self.stop_button.config(state=tk.DISABLED)
        
        # Buffer size
        buffer_frame = ttk.Frame(controls_frame)
        buffer_frame.grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W)
        
        ttk.Label(buffer_frame, text="Buffer Size:").pack(side=tk.LEFT, padx=5)
        
        self.buffer_size = tk.StringVar(value="512")
        buffer_sizes = ["128", "256", "512", "1024", "2048"]
        buffer_combo = ttk.Combobox(buffer_frame, textvariable=self.buffer_size, values=buffer_sizes, width=8, state="readonly")
        buffer_combo.pack(side=tk.LEFT, padx=5)
        
        # Test tone checkbox
        self.test_tones = tk.BooleanVar(value=False)
        test_check = ttk.Checkbutton(buffer_frame, text="Include test tones", variable=self.test_tones)
        test_check.pack(side=tk.LEFT, padx=20)
        
        # Progress bar
        self.progress_bar_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            self.main_frame, 
            orient="horizontal", 
            mode="determinate",
            variable=self.progress_bar_var
        )
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)
    
    def _create_timeline_frame(self, window_width):
        """Create timeline visualization."""
        timeline_frame = ttk.LabelFrame(self.main_frame, text="Experiment Timeline", padding="10")
        timeline_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # First half timeline
        timeline_label1 = ttk.Label(timeline_frame, text="First Half:")
        timeline_label1.pack(anchor=tk.W, pady=(0, 2))
        
        self.timeline_canvas1 = tk.Canvas(timeline_frame, width=window_width-60, height=60, bg="white")
        self.timeline_canvas1.pack(fill=tk.X, pady=2)
        
        # Second half timeline
        timeline_label2 = ttk.Label(timeline_frame, text="Second Half:")
        timeline_label2.pack(anchor=tk.W, pady=(5, 2))
        
        self.timeline_canvas2 = tk.Canvas(timeline_frame, width=window_width-60, height=60, bg="white")
        self.timeline_canvas2.pack(fill=tk.X, pady=2)
        
        # Create timelines
        timeline_start_x = 50
        self.timeline_end_x = window_width - 110
        
        # First timeline
        self.timeline_y1 = 30
        self.timeline_canvas1.create_line(timeline_start_x, self.timeline_y1, self.timeline_end_x, self.timeline_y1, width=2)
        self.timeline_canvas1.create_text(timeline_start_x - 40, self.timeline_y1, text="0:00", font=("Arial", 8))
        
        # First timeline progress indicator
        self.progress_line1 = self.timeline_canvas1.create_line(
            timeline_start_x, self.timeline_y1 - 15, timeline_start_x, self.timeline_y1 + 15, 
            width=3, fill="green", state="hidden"
        )
        
        # Second timeline
        self.timeline_y2 = 30
        self.timeline_canvas2.create_line(timeline_start_x, self.timeline_y2, self.timeline_end_x, self.timeline_y2, width=2)
        
        # Second timeline progress indicator
        self.progress_line2 = self.timeline_canvas2.create_line(
            timeline_start_x, self.timeline_y2 - 15, timeline_start_x, self.timeline_y2 + 15, 
            width=3, fill="green", state="hidden"
        )
    
    def _create_click_area(self, window_width):
        """Create area for mouse click tracking."""
        click_frame = ttk.LabelFrame(self.main_frame, text="Mouse Click Area", padding="10")
        click_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Canvas for visualizing mouse clicks
        self.click_canvas = tk.Canvas(click_frame, bg="lightyellow")
        self.click_canvas.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Add text to the click area
        self.click_canvas.create_text(
            window_width//2 - 30, 50,
            text="CLICK HERE WHEN YOU HEAR THE TACTILE STIMULUS",
            font=("Arial", 14, "bold"), fill="blue"
        )
        
        # Add click counter
        self.click_counter_text = self.click_canvas.create_text(
            100, 20,
            text="Clicks: 0",
            font=("Arial", 12), fill="black"
        )
        
        # Bind mouse clicks
        self.click_canvas.bind("<Button-1>", self.on_mouse_click)
    
    def _create_log_area(self):
        """Create log text area"""
        log_frame = ttk.LabelFrame(self.main_frame, text="Log", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.log_text = tk.Text(log_frame, height=8, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=log_scrollbar.set)
    
    def refresh_participant(self):
        """Refresh participant information from metadata."""
        debug_print("Refreshing participant information...", 0, self.log_text)
        self.load_participant_from_metadata()
    
    def get_start_time(self):
        """Get the start time in seconds from the minute/second inputs"""
        try:
            minutes = int(self.minutes_var.get())
            seconds = int(self.seconds_var.get())
            return minutes * 60 + seconds
        except ValueError:
            return 0
    
    def start_playback(self):
        """Start playing the audio file."""
        if not self.audio_file_path:
            messagebox.showinfo("Selection Required", "No audio file loaded for participant.")
            return
        
        # Get buffer size
        try:
            buffer_size = int(self.buffer_size.get())
        except:
            buffer_size = 512
        
        # Get start time
        start_time = self.get_start_time()
        
        # Update UI
        self.play_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL, text="Pause")
        self.stop_button.config(state=tk.NORMAL)
        self.status_var.set(f"Loading: {os.path.basename(self.audio_file_path)}")
        
        # Reset click counter
        self.click_count = 0
        self.mouse_clicks = []
        self.click_canvas.itemconfig(self.click_counter_text, text=f"Clicks: 0")
        
        # Clear timeline
        self.clear_timeline()
        
        # Add tactile markers to timeline
        for t_time in self.tactile_times:
            self.add_timeline_marker(t_time, "blue")
        
        # Cancel any existing progress updates
        if self.update_progress_id is not None:
            self.root.after_cancel(self.update_progress_id)
            self.update_progress_id = None
        
        # Start playback in a separate thread
        def load_and_play():
            try:
                # Load the file
                if not self.audio_player.load_file(self.audio_file_path, self.log_text):
                    self.root.after(0, lambda: self.status_var.set("Error loading file"))
                    self.root.after(0, lambda: self.reset_ui())
                    return
                
                # Prepare for playback
                if not self.audio_player.prepare_playback(
                    self.log_text, buffer_size=buffer_size, test_tones=self.test_tones.get()):
                    self.root.after(0, lambda: self.status_var.set("Error preparing playback"))
                    self.root.after(0, lambda: self.reset_ui())
                    return
                
                # Record start time for experiment timeline
                self.start_time = time.perf_counter()
                
                # Send LSL marker if available
                if self.lsl_outlet and self.participant_info:
                    self.lsl_outlet.push_sample([f"start:{self.participant_info['participant_id']}"])
                
                # Start playback
                if not self.audio_player.start_playback(self.log_text, buffer_size=buffer_size, start_time=start_time):
                    self.root.after(0, lambda: self.status_var.set("Error starting playback"))
                    self.root.after(0, lambda: self.reset_ui())
                    return
                
                # Start progress updates
                self.experiment_running = True
                self.root.after(0, self.update_progress)
                self.root.after(0, lambda: self.status_var.set(f"Playing: {os.path.basename(self.audio_file_path)}"))
            except Exception as e:
                self.root.after(0, lambda: debug_print(f"Error in playback thread: {e}", 0, self.log_text))
                self.root.after(0, lambda: self.reset_ui())
                self.root.after(0, lambda: self.status_var.set("Error occurred during playback"))
        
        threading.Thread(target=load_and_play, daemon=True).start()
    
    def pause_resume(self):
        """Pause or resume playback"""
        if not self.audio_player.is_playing:
            debug_print("Nothing playing to pause/resume", 0, self.log_text)
            return
            
        if self.audio_player.is_paused:
            if self.audio_player.resume_playback(self.log_text):
                self.pause_button.config(text="Pause")
                self.status_var.set(f"Playing: {os.path.basename(self.audio_file_path)}")
            else:
                self.stop_playback()
        else:
            if self.audio_player.pause_playback(self.log_text):
                self.pause_button.config(text="Resume")
                self.status_var.set(f"Paused: {os.path.basename(self.audio_file_path)}")
            else:
                self.stop_playback()
    
    def stop_playback(self):
        """Stop playback"""
        self.experiment_running = False
        
        if self.audio_player.stop_playback(self.log_text):
            # Full reset to ensure clean state
            self.audio_player.reset()
            
            # Save click data
            self.save_click_data()
            
            # Send LSL marker if available
            if self.lsl_outlet and self.participant_info:
                self.lsl_outlet.push_sample([f"stop:{self.participant_info['participant_id']}"])
            
            # Reset UI
            self.reset_ui()
            self.status_var.set("Stopped")
            debug_print("Playback fully stopped and reset", 0, self.log_text)
    
    def on_mouse_click(self, event):
        """Handle mouse click events during the experiment."""
        if not self.experiment_running or self.start_time is None:
            return
            
        # Calculate time since experiment start
        current_time = time.perf_counter() - self.start_time
        
        # Add to mouse clicks list
        self.mouse_clicks.append({
            "time": current_time,
            "timestamp": datetime.datetime.now().isoformat(),
            "x": event.x,
            "y": event.y,
            "participant_id": self.participant_info["participant_id"] if self.participant_info else "unknown"
        })
        
        # Update click count
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
        
        # Add click marker to timeline
        self.add_timeline_marker(current_time, "red")
        
        # Send LSL marker
        if self.lsl_outlet:
            self.lsl_outlet.push_sample([f"click:{current_time:.3f}"])
        
        debug_print(f"Mouse click at {current_time:.3f} seconds", 0, self.log_text)
    
    def reset_ui(self):
        """Reset UI elements after playback finishes"""
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED, text="Pause")
        self.stop_button.config(state=tk.DISABLED)
        
        # Cancel progress updates
        if self.update_progress_id is not None:
            self.root.after_cancel(self.update_progress_id)
            self.update_progress_id = None
    
    def update_progress(self):
        """Update progress display and timeline"""
        if not self.audio_player.is_playing:
            self.reset_ui()
            self.progress_var.set("00:00 / 00:00")
            self.experiment_running = False
            return
        
        try:
            # Get current position
            position = self.audio_player.get_position()
            duration = self.audio_player.get_duration()
            
            # Format time
            pos_min = int(position) // 60
            pos_sec = int(position) % 60
            dur_min = int(duration) // 60
            dur_sec = int(duration) % 60
            
            self.progress_var.set(f"{pos_min:02d}:{pos_sec:02d} / {dur_min:02d}:{dur_sec:02d}")
            
            # Update progress bar
            progress_percent = min(100, (position / duration) * 100)
            self.progress_bar_var.set(progress_percent)
            
            # Update timeline progress
            self.update_timeline_progress(position)
            
            # Schedule next update - but don't accumulate if there are delays
            if self.update_progress_id is not None:
                self.root.after_cancel(self.update_progress_id)
            
            # Check again if still playing before scheduling next update
            if self.audio_player.is_playing:
                self.update_progress_id = self.root.after(200, self.update_progress)
            else:
                self.reset_ui()
                self.status_var.set("Ready")
                self.experiment_running = False
                
        except Exception as e:
            debug_print(f"Error updating progress: {e}", 0, self.log_text)
            # Schedule recovery update
            self.update_progress_id = self.root.after(500, self.update_progress)
    
    def add_timeline_marker(self, time_sec, color):
        """Add a marker to the timeline at the specified time."""
        if self.audio_duration <= 0:
            return
            
        timeline_start_x = 50
        timeline_width = self.timeline_end_x - timeline_start_x
        
        # Calculate the halfway point of the audio
        halfway_time = self.audio_duration / 2
        
        # Determine which timeline to use based on time
        if time_sec <= halfway_time:
            # First half timeline
            x_pos = timeline_start_x + (time_sec / halfway_time) * timeline_width
            
            # Create marker
            marker = self.timeline_canvas1.create_oval(
                x_pos-4, self.timeline_y1-4, x_pos+4, self.timeline_y1+4, 
                fill=color, outline="black", width=1
            )
            self.timeline_markers1.append(marker)
        else:
            # Second half timeline - adjust position to start from beginning of second timeline
            adjusted_time = time_sec - halfway_time
            x_pos = timeline_start_x + (adjusted_time / halfway_time) * timeline_width
            
            # Create marker
            marker = self.timeline_canvas2.create_oval(
                x_pos-4, self.timeline_y2-4, x_pos+4, self.timeline_y2+4, 
                fill=color, outline="black", width=1
            )
            self.timeline_markers2.append(marker)
    
    def update_timeline_progress(self, elapsed_time):
        """Update timeline progress lines."""
        if self.audio_duration <= 0:
            return
            
        # Calculate the halfway point of the audio
        halfway_time = self.audio_duration / 2
        
        # Update the appropriate timeline progress line
        timeline_start_x = 50
        timeline_width = self.timeline_end_x - timeline_start_x
        
        if elapsed_time <= halfway_time:
            # First half timeline
            x_pos = timeline_start_x + (elapsed_time / halfway_time) * timeline_width
            
            self.timeline_canvas1.coords(
                self.progress_line1, 
                x_pos, self.timeline_y1 - 15, 
                x_pos, self.timeline_y1 + 15
            )
            self.timeline_canvas1.itemconfig(self.progress_line1, state="normal")
            
            # Hide second timeline progress line when in first half
            self.timeline_canvas2.itemconfig(self.progress_line2, state="hidden")
        else:
            # Second half timeline
            # Keep first timeline progress line at the end
            self.timeline_canvas1.coords(
                self.progress_line1, 
                self.timeline_end_x, self.timeline_y1 - 15, 
                self.timeline_end_x, self.timeline_y1 + 15
            )
            
            # Update second timeline progress line
            adjusted_time = elapsed_time - halfway_time
            x_pos = timeline_start_x + (adjusted_time / halfway_time) * timeline_width
            
            self.timeline_canvas2.coords(
                self.progress_line2, 
                x_pos, self.timeline_y2 - 15, 
                x_pos, self.timeline_y2 + 15
            )
            self.timeline_canvas2.itemconfig(self.progress_line2, state="normal")
    
    def clear_timeline(self):
        """Clear all markers from both timelines."""
        # Clear first timeline
        for marker in self.timeline_markers1:
            self.timeline_canvas1.delete(marker)
        self.timeline_markers1 = []
        
        # Hide first progress line
        self.timeline_canvas1.itemconfig(self.progress_line1, state="hidden")
        
        # Clear second timeline
        for marker in self.timeline_markers2:
            self.timeline_canvas2.delete(marker)
        self.timeline_markers2 = []
        
        # Hide second progress line
        self.timeline_canvas2.itemconfig(self.progress_line2, state="hidden")
    
    def parse_timestamp(self, timestamp_str):
        """Parse timestamp in format MM:SS.S to seconds."""
        if timestamp_str is None or timestamp_str == "":
            return None
            
        match = re.match(r'(\d+):(\d+\.\d+)', timestamp_str)
        if match:
            minutes, seconds = match.groups()
            return float(minutes) * 60 + float(seconds)
        return None
    
    def load_tactile_times(self, participant_num):
        """Load tactile stimulus times from the design file."""
        try:
            # Load from design file
            design_file = os.path.join(EXPERIMENT_LOG_DIR, f"participant_{participant_num}_design.csv")
            debug_print(f"Loading trial data from: {design_file}", 0, self.log_text)
            
            if not os.path.exists(design_file):
                debug_print(f"Design file not found: {design_file}", 0, self.log_text)
                return
            
            # Use csv module
            tactile_times = []
            
            with open(design_file, 'r', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Check if this is not a catch trial
                    if row.get('trial_type', '') != 'catch':
                        # Extract tactile stimulus timestamp
                        ts_str = row.get('tactile_stimulus_timestamp', '')
                        if ts_str and ts_str.strip():  # Check if not empty
                            time_sec = self.parse_timestamp(ts_str)
                            if time_sec is not None:
                                tactile_times.append(time_sec)
            
            self.tactile_times = tactile_times
            debug_print(f"Loaded {len(tactile_times)} tactile stimulus times", 0, self.log_text)
        except Exception as e:
            debug_print(f"Error loading tactile times: {e}", 0, self.log_text)
            traceback.print_exc()
            self.tactile_times = []
    
    def update_timeline_with_duration(self):
        """Update both timelines with markers based on audio duration."""
        if self.audio_duration <= 0:
            return
            
        # Clear existing time markers
        for canvas in [self.timeline_canvas1, self.timeline_canvas2]:
            for item in canvas.find_all():
                if canvas.type(item) == "text" and item != self.click_counter_text:
                    canvas.delete(item)
        
        # Draw timelines again to ensure they're clear
        timeline_start_x = 50
        timeline_width = self.timeline_end_x - timeline_start_x
        
        # First timeline base line
        self.timeline_canvas1.create_line(timeline_start_x, self.timeline_y1, 
                                        self.timeline_end_x, self.timeline_y1, width=2)
        
        # Second timeline base line
        self.timeline_canvas2.create_line(timeline_start_x, self.timeline_y2, 
                                        self.timeline_end_x, self.timeline_y2, width=2)
        
        # Calculate halfway point
        halfway_time = self.audio_duration / 2
        
        # Determine a suitable interval based on duration
        if halfway_time < 30:  # Less than 30 seconds per timeline
            interval = 5  # 5 second intervals
        elif halfway_time < 120:  # Less than 2 minutes per timeline
            interval = 15  # 15 second intervals
        else:
            interval = 30  # 30 second intervals
        
        # Add time markers to first timeline
        for sec in range(0, int(halfway_time) + interval, interval):
            if sec > halfway_time:
                break
                
            x_pos = timeline_start_x + (sec / halfway_time) * timeline_width
            
            # Create tick mark
            self.timeline_canvas1.create_line(x_pos, self.timeline_y1 - 5, 
                                           x_pos, self.timeline_y1 + 5, width=1)
            
            # Format time label
            if sec >= 60:
                # Show as minutes:seconds for longer intervals
                mins = sec // 60
                secs = sec % 60
                label = f"{mins}:{secs:02d}"
            else:
                # Show as seconds for shorter intervals
                label = f"{sec}s"
                
            self.timeline_canvas1.create_text(x_pos, self.timeline_y1 + 12, 
                                           text=label, font=("Arial", 8))
        
        # Add time markers to second timeline
        for sec in range(0, int(halfway_time) + interval, interval):
            if sec > halfway_time:
                break
                
            x_pos = timeline_start_x + (sec / halfway_time) * timeline_width
            
            # Calculate actual time (offset by halfway point)
            actual_sec = sec + int(halfway_time)
            
            # Create tick mark
            self.timeline_canvas2.create_line(x_pos, self.timeline_y2 - 5, 
                                           x_pos, self.timeline_y2 + 5, width=1)
            
            # Format time label
            if actual_sec >= 60:
                # Show as minutes:seconds for longer intervals
                mins = actual_sec // 60
                secs = actual_sec % 60
                label = f"{mins}:{secs:02d}"
            else:
                # Show as seconds for shorter intervals
                label = f"{actual_sec}s"
                
            self.timeline_canvas2.create_text(x_pos, self.timeline_y2 + 12, 
                                           text=label, font=("Arial", 8))
        
        # Add tactile event markers
        for t_time in self.tactile_times:
            self.add_timeline_marker(t_time, "blue")
    
    def save_click_data(self):
        """Save the mouse click data to CSV."""
        if not self.mouse_clicks:
            debug_print("No click data to save", 0, self.log_text)
            return
            
        try:
            # Create timestamp for the filename
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Use participant ID if available
            participant_id = self.participant_info["participant_id"] if self.participant_info else "unknown"
            participant_num = self.participant_info["participant_num"] if self.participant_info else "0"
            
            filename = os.path.join(RESULTS_DIR, f"participant_{participant_num}_clicks_{timestamp}.csv")
            
            # Write click data to CSV
            with open(filename, 'w', newline='') as csvfile:
                # Determine fieldnames from the first click dictionary
                fieldnames = list(self.mouse_clicks[0].keys())
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                # Write all click data
                for click in self.mouse_clicks:
                    writer.writerow(click)
            
            debug_print(f"Saved click data to {filename}", 0, self.log_text)
            self.status_var.set(f"Click data saved to {os.path.basename(filename)}")
            
        except Exception as e:
            debug_print(f"Error saving click data: {e}", 0, self.log_text)
            self.status_var.set(f"Error saving click data: {str(e)}")
    
    def on_closing(self):
        """Handle window closing event."""
        debug_print("Application closing, stopping all audio playback...", 0, self.log_text)
        
        # Cancel any scheduled updates
        if self.update_progress_id is not None:
            try:
                self.root.after_cancel(self.update_progress_id)
            except:
                pass
            self.update_progress_id = None
        
        # Stop any ongoing playback
        try:
            self.audio_player.reset()
        except:
            pass
        
        # Add a small delay to ensure streams are closed
        time.sleep(0.2)
        
        # Force stop any remaining audio output
        try:
            sd.stop()
        except:
            pass
        
        # Destroy the window and exit
        debug_print("Application closed.", 0, self.log_text)
        self.root.destroy()

def main():
    # Clean up on startup just in case
    try:
        sd.stop()
    except:
        pass
    
    # Ensure sounddevice is configured for low latency
    sd.default.latency = 'low'
        
    root = tk.Tk()
    
    try:
        app = BreathingSpaceApp(root)
        root.mainloop()
    except Exception as e:
        print(f"Critical error: {e}")
        traceback.print_exc()
        # Attempt clean shutdown
        try:
            sd.stop()
        except:
            pass
    finally:
        # Final cleanup
        print("Application shutting down, performing final cleanup...")
        try:
            sd.stop()
        except:
            pass

if __name__ == "__main__":
    main()