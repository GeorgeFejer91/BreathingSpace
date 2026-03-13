#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Unified Breathing Space Experiment Application

Combines:
- Audio playback with timeline visualization
- Mouse click capturing for tactile stimulus responses
- LSL streaming of mouse clicks and audio input
- Automatic LSL recording of all participant streams
- Automatic participant detection from metadata

Author: May 2025
"""

import os
import sys
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import sounddevice as sd
import soundfile as sf
import numpy as np
import threading
import datetime as dt
import re
import glob
import traceback
import csv
import json
from pathlib import Path
import ctypes
from ctypes import wintypes, byref

# ──────────────────────────────────────────────────────────────────────────────
#  LSL Integration (Optional)
# ──────────────────────────────────────────────────────────────────────────────
try:
    from pylsl import StreamInfo, StreamOutlet, resolve_streams
    from liesl import Recorder
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    print("WARNING: LSL libraries not found. LSL functionality will be disabled.")
    print("To enable LSL features, install: pylsl liesl")

# ──────────────────────────────────────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────────────────────────────────────
# File paths
BASE_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace"
EXPERIMENT_AUDIO_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentAudio_2ch")
EXPERIMENT_LOG_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentLog")
RESULTS_DIR = os.path.join(BASE_DIR, "Level2_RunExperiment", "Results")
METADATA_DIR = os.path.join(RESULTS_DIR, "participant_metadata")
LSL_OUTPUT_DIR = os.path.join(RESULTS_DIR, "LSL_Output")

# Audio Configuration
AUDIO_SAMPLE_RATE = 44100    # Hz (fallback to device default if invalid)
AUDIO_CHANNELS = 2           # stereo
AUDIO_CHUNK_SIZE = 1024      # frames / block
AUDIO_IN_NAME = "Input 3/4"  # Audio input device name substring
AUDIO_OUT_NAME = "Output"    # Audio output device name substring

# Mouse Configuration
POLL_INTERVAL = 0.005        # mouse polling interval (s)

# Click Tone Configuration
TONE_FREQUENCY = 800         # Hz
TONE_DURATION = 0.1          # seconds
TONE_AMPLITUDE = 0.25        # 0.0-1.0

# Ensure directories exist
for directory in [RESULTS_DIR, METADATA_DIR, LSL_OUTPUT_DIR]:
    os.makedirs(directory, exist_ok=True)

# Configure sounddevice for low latency
sd.default.latency = 'low'

# ──────────────────────────────────────────────────────────────────────────────
#  Helper Classes
# ──────────────────────────────────────────────────────────────────────────────
class Logger:
    """Handles logging to both console and text widget"""
    def __init__(self, text_widget=None):
        self.text_widget = text_widget
        self.log_buffer = []
        
    def set_widget(self, text_widget):
        """Set or update the text widget"""
        self.text_widget = text_widget
        # Display buffered logs if any
        if self.log_buffer and self.text_widget:
            for msg, tags in self.log_buffer:
                self._write_to_widget(msg, tags)
            self.log_buffer = []
            
    def log(self, message, level="info"):
        """Log a message with timestamp and optional level"""
        timestamp = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        formatted_msg = f"[{timestamp}] {message}"
        print(formatted_msg)
        
        # Determine tags based on level
        tags = ()
        if level == "error":
            tags = ("error",)
        elif level == "warning":
            tags = ("warning",)
        elif level == "success":
            tags = ("success",)
        
        # Store in buffer or write to widget
        if self.text_widget and self.text_widget.winfo_exists():
            self._write_to_widget(formatted_msg, tags)
        else:
            self.log_buffer.append((formatted_msg, tags))
            
    def _write_to_widget(self, message, tags=()):
        """Write message to text widget with specified tags"""
        if not self.text_widget or not self.text_widget.winfo_exists():
            return
            
        # Use after to avoid threading issues
        self.text_widget.after(0, lambda: self._insert_text(message, tags))
    
    def _insert_text(self, message, tags):
        """Insert text into widget (should be called from main thread)"""
        try:
            self.text_widget.config(state=tk.NORMAL)
            self.text_widget.insert(tk.END, message + "\n", tags)
            self.text_widget.see(tk.END)
            self.text_widget.config(state=tk.DISABLED)
        except Exception as e:
            print(f"Error writing to log widget: {e}")

class AudioPlayer:
    """Handles audio playback with specific device routing"""
    def __init__(self, logger):
        self.logger = logger
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

    def find_audio_devices(self):
        """Find Komplete Audio ASIO or other suitable devices"""
        # Get device list
        devices = sd.query_devices()
        self.logger.log(f"Found {len(devices)} audio devices")
        
        # Look for Komplete Audio ASIO devices
        self.output_12_id = None
        self.output_34_id = None
        
        for i, device in enumerate(devices):
            if device['max_output_channels'] > 0:
                # Check if it's an ASIO device
                if 'hostapi' in device and device['hostapi'] == 3:  # ASIO
                    name = device['name'].lower()
                    self.logger.log(f"Found ASIO device {i}: {device['name']}")
                    if "output 1/2" in name and ("komplete" in name or "1/2" in name):
                        self.output_12_id = i
                        self.logger.log(f"Found Output 1/2 ASIO device: ID {i} - {device['name']}")
                    elif "output 3/4" in name and ("komplete" in name or "3/4" in name):
                        self.output_34_id = i
                        self.logger.log(f"Found Output 3/4 ASIO device: ID {i} - {device['name']}")
        
        # Check if we found the devices
        if self.output_12_id is None or self.output_34_id is None:
            self.logger.log("Could not find both ASIO devices for Komplete Audio", level="warning")
            self.logger.log("Looking for any suitable multi-channel device instead")
            
            # Try to find any multi-channel device as a fallback
            for i, device in enumerate(devices):
                if device['max_output_channels'] >= 4 and 'hostapi' in device and device['hostapi'] == 3:
                    self.logger.log(f"Found multi-channel ASIO device: {device['name']}")
                    self.output_12_id = i  # Use the same device for both outputs
                    self.output_34_id = i
                    break
            
            if self.output_12_id is None:
                self.logger.log("No suitable audio output device found.", level="error")
                return False
                
        return True

    def load_file(self, file_path):
        """Load audio file and prepare for playback"""
        # First, reset the player state
        self.reset()
        
        try:
            self.logger.log(f"Loading file: {file_path}")
            audio, file_sr = sf.read(file_path, always_2d=True, dtype='float32')
            self.logger.log(f"File loaded: {len(audio)} samples, {file_sr}Hz, {audio.shape[1]} channels")
            
            self.audio_data = audio
            self.sample_rate = file_sr
            self.current_position = 0
            return True
        except Exception as e:
            self.logger.log(f"Error loading audio file: {e}", level="error")
            self.reset()  # Reset on error
            return False

    def prepare_playback(self, buffer_size=512, test_tones=False):
        """Prepare audio devices and data for playback"""
        # Make sure devices are found
        if self.output_12_id is None:
            if not self.find_audio_devices():
                return False
        
        # Get device sample rate
        try:
            device_info = sd.query_devices(self.output_12_id)
            device_sr = int(device_info['default_samplerate'])
        except:
            device_sr = 44100  # Default to 44.1kHz if query fails
        
        self.logger.log(f"Using sample rate: {device_sr}Hz")
        self.logger.log(f"Using buffer size: {buffer_size} samples")
        
        # Resample if needed
        if self.sample_rate != device_sr:
            self.logger.log(f"Sample rate mismatch: file={self.sample_rate}Hz, device={device_sr}Hz")
            self.audio_data = self._resample(self.audio_data, self.sample_rate, device_sr)
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
            self.logger.log(f"Prepended test tones: 440Hz on left, 880Hz on right ({test_duration} seconds)")
        
        return True

    def _resample(self, audio, orig_sr, target_sr):
        """Simple resampling function using linear interpolation"""
        self.logger.log(f"Resampling audio from {orig_sr}Hz to {target_sr}Hz...")
        
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
        
        self.logger.log(f"Resampling complete. Original samples: {len(audio)}, New samples: {len(resampled)}")
        return resampled

    def start_playback(self, buffer_size=512, start_time=0.0):
        """Start audio playback from specified time position"""
        # First stop any existing playback
        if self.is_playing:
            self.stop_playback()
            # Short delay to ensure cleanup is complete
            time.sleep(0.1)
            
        if self.audio_data is None:
            self.logger.log("No audio data loaded", level="error")
            return False
            
        # Calculate start position in samples
        start_sample = int(start_time * self.sample_rate)
        if start_sample >= len(self.audio_data):
            self.logger.log(f"Start time ({start_time}s) exceeds audio duration", level="warning")
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
                args=(left_output, right_output, buffer_size, start_sample),
                daemon=True
            )
            
            self.is_playing = True
            self.is_paused = False
            self.playback_thread.start()
            
            self.logger.log(f"Playback started from position {start_time:.2f}s", level="success")
            return True
        except Exception as e:
            self.logger.log(f"Error starting playback: {e}", level="error")
            self.is_playing = False
            self.is_paused = False
            return False
        
    def _playback_thread_func(self, left_output, right_output, buffer_size, start_sample):
        """Thread function for audio playback"""
        try:
            # Truncate outputs to start at the desired position
            left_output = left_output[start_sample:]
            right_output = right_output[start_sample:]
            
            # Set up callback functions for streaming
            def left_callback(outdata, frames, time_info, status):
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
            
            def right_callback(outdata, frames, time_info, status):
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
            self.logger.log(f"Error in playback thread: {e}", level="error")
        
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
                
            was_playing = self.is_playing
            self.is_playing = False
            self.is_paused = False
            
            if was_playing:
                self.logger.log("Playback complete")
    
    def pause_playback(self):
        """Pause audio playback"""
        if self.is_playing and not self.is_paused:
            self.pause_event.set()
            self.is_paused = True
            self.logger.log("Playback paused")
            return True
        return False
    
    def resume_playback(self):
        """Resume audio playback"""
        if self.is_playing and self.is_paused:
            self.pause_event.clear()
            self.is_paused = False
            self.logger.log("Playback resumed")
            return True
        return False
    
    def stop_playback(self):
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
                self.logger.log(f"Error closing left stream: {e}", level="warning")
            self.left_stream = None
                
        if hasattr(self, 'right_stream') and self.right_stream:
            try:
                self.right_stream.stop()
                self.right_stream.close()
            except Exception as e:
                self.logger.log(f"Error closing right stream: {e}", level="warning")
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
        
        if was_playing:
            self.logger.log("Playback stopped")
            
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

class ToneGenerator:
    """Generates and plays tones on mouse clicks"""
    def __init__(self, logger, frequency=TONE_FREQUENCY, duration=TONE_DURATION, 
                 sample_rate=AUDIO_SAMPLE_RATE, amplitude=TONE_AMPLITUDE):
        self.logger = logger
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
                    if AUDIO_OUT_NAME.lower() in name and "3/4" in name:
                        self.output_id = i
                        self.logger.log(f"Found tone output device: ID {i} - {device['name']}")
                        return
            
            self.logger.log(f"Output 3/4 not found. Using default output device ID {self.output_id}", level="warning")
        except Exception as e:
            self.logger.log(f"Error finding tone output device: {e}", level="error")
    
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
            self.logger.log(f"Tone output stream opened on device {self.output_id}")
            
            # Position in the tone data
            self.position = 0
            
        except Exception as e:
            self.logger.log(f"Error opening tone output stream: {e}", level="error")
            self.stream = None
    
    def _stream_callback(self, outdata, frames, time, status):
        """Audio stream callback"""
        if status and status.output_underflow:
            # Don't log buffer underflows as they're common
            pass
        elif status:
            self.logger.log(f"Tone output stream status: {status}", level="warning")
            
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
                self.logger.log("Cannot play tone: no active audio stream", level="warning")
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

class LSLManager:
    """Manages LSL streaming and recording functionality"""
    def __init__(self, logger):
        self.logger = logger
        self.mouse_stream = None
        self.audio_stream = None
        self.recorder = None
        self.streaming = False
        self.recording = False
        self.audio_device_id = None
        self.audio_running = False
        self.audio_thread = None
        self.mouse_thread = None
        self.audio_sample_rate = AUDIO_SAMPLE_RATE
        self.start_time = None
        self.click_count = 0
        
        # Mouse state tracking
        self.last_left = self.last_right = self.last_middle = False
        
        # Stream names for verification
        self.mouse_stream_name = ""
        self.audio_stream_name = ""
        
        # Streams to record
        self.matched_streams = []
        self.verified = False
    
    def is_available(self):
        """Check if LSL functionality is available"""
        return LSL_AVAILABLE
    
    def find_audio_device(self):
        """Find the Audio Input 3/4 device"""
        if not LSL_AVAILABLE:
            return False
            
        try:
            devices = sd.query_devices()
            
            matches = [(i, d) for i, d in enumerate(devices)
                       if AUDIO_IN_NAME.lower() in d["name"].lower() and 
                          d["max_input_channels"] >= AUDIO_CHANNELS]
                          
            if not matches:
                self.logger.log(f"No audio input containing '{AUDIO_IN_NAME}'", level="warning")
                return False
                
            self.audio_device_id, info = matches[0]
            self.logger.log(f"Found LSL audio input → {info['name']} (ID {self.audio_device_id})")
            return True
        except Exception as e:
            self.logger.log(f"Error finding audio input device: {e}", level="error")
            return False
    
    def create_streams(self, participant_info):
        """Create LSL streams for mouse clicks and audio"""
        if not LSL_AVAILABLE:
            self.logger.log("LSL libraries not available", level="error")
            return False
            
        if not participant_info:
            self.logger.log("Cannot create LSL streams without participant info", level="error")
            return False
        
        try:
            participant_id = participant_info.get("participant_id", "Unknown")
            
            # Create a unique identifier
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Mouse stream (3 channels: time, x, y coordinates)
            self.mouse_stream_name = f"MouseClicks_{participant_id}_{ts}"
            m_info = StreamInfo(
                self.mouse_stream_name, 
                "MouseEvents", 
                3, 
                0,  # irregular sampling rate
                "float32", 
                f"mouse_{participant_id}_{ts}"
            )
            
            # Add participant metadata to stream info
            desc = m_info.desc()
            desc.append_child_value("participant_id", participant_id)
            for key, value in participant_info.items():
                if key != "participant_id":  # Already added
                    desc.append_child_value(key, str(value))
            
            self.mouse_stream = StreamOutlet(m_info)
            self.logger.log(f"Mouse LSL stream created: {self.mouse_stream_name}", level="success")
            
            # Check if we have an audio device
            if self.audio_device_id is None:
                if not self.find_audio_device():
                    self.logger.log("No audio input found for LSL streaming", level="warning")
                    # Continue without audio stream - it's optional
                    return True
            
            # Audio stream 
            self.audio_stream_name = f"Audio_{participant_id}_{ts}"
            a_info = StreamInfo(
                self.audio_stream_name, 
                "Audio",
                AUDIO_CHANNELS, 
                self.audio_sample_rate,
                "float32", 
                f"audio_{participant_id}_{ts}"
            )
            
            # Add participant metadata to audio stream info
            a_desc = a_info.desc()
            a_desc.append_child_value("participant_id", participant_id)
            for key, value in participant_info.items():
                if key != "participant_id":  # Already added
                    a_desc.append_child_value(key, str(value))
            
            self.audio_stream = StreamOutlet(a_info)
            self.logger.log(f"Audio LSL stream created: {self.audio_stream_name}", level="success")
            
            # Verify the streams are discoverable
            return self.verify_streams()
            
        except Exception as e:
            self.logger.log(f"Error creating LSL streams: {e}", level="error")
            return False
    
    def verify_streams(self):
        """Verify that our created streams are discoverable via LSL"""
        if not LSL_AVAILABLE:
            return False
            
        self.logger.log("Verifying LSL streams can be discovered...")
        self.verified = False
        
        # Give streams time to register with LSL
        time.sleep(0.5)
        
        try:
            # Get all available LSL streams
            all_streams = resolve_streams(wait_time=2.0)
            
            if not all_streams:
                self.logger.log("No LSL streams found during verification", level="warning")
                return False
            
            # Check if our streams are among the discovered streams
            mouse_found = False
            audio_found = False
            
            for stream in all_streams:
                if stream.name() == self.mouse_stream_name:
                    mouse_found = True
                    self.logger.log(f"Mouse stream verified: {self.mouse_stream_name}")
                
                if stream.name() == self.audio_stream_name:
                    audio_found = True
                    self.logger.log(f"Audio stream verified: {self.audio_stream_name}")
            
            # Audio stream is optional
            if mouse_found and (audio_found or self.audio_stream is None):
                self.logger.log("LSL streams successfully verified", level="success")
                self.verified = True
                return True
            else:
                missing = []
                if not mouse_found:
                    missing.append("mouse")
                if not audio_found and self.audio_stream is not None:
                    missing.append("audio")
                
                self.logger.log(f"LSL verification failed: {', '.join(missing)} stream(s) not found", level="warning")
                return False
                
        except Exception as e:
            self.logger.log(f"Error verifying LSL streams: {e}", level="error")
            return False
            
    def scan_participant_streams(self, participant_info):
        """Scan for LSL streams matching this participant"""
        if not LSL_AVAILABLE:
            return False
            
        if not participant_info:
            return False
            
        # Clear streams list but keep any entries we know are ours
        self.matched_streams = []
        
        # Get participant ID without the "P" prefix
        participant_id = participant_info.get("participant_id", "Unknown")
        participant_num = participant_id.replace("P", "").strip()
        
        try:
            # Get active streams with a timeout
            all_streams = resolve_streams(wait_time=1.0)
            
            if not all_streams:
                self.logger.log("No LSL streams found during scan", level="warning")
                return False
            
            # Add our specific streams first to ensure they're included
            found_mouse = False
            found_audio = False
            
            # First pass: find our specific streams
            for stream in all_streams:
                if self.mouse_stream and stream.name() == self.mouse_stream_name:
                    self.matched_streams.append(stream)
                    found_mouse = True
                    
                elif self.audio_stream and stream.name() == self.audio_stream_name:
                    self.matched_streams.append(stream)
                    found_audio = True
            
            # Report on our specific streams
            if self.mouse_stream and not found_mouse:
                self.logger.log("Warning: Our mouse stream was not found in scan", level="warning")
            if self.audio_stream and not found_audio:
                self.logger.log("Warning: Our audio stream was not found in scan", level="warning")
            
            # Filter remaining streams for participant ID
            for stream in all_streams:
                # Skip if already added
                if (self.mouse_stream and stream.name() == self.mouse_stream_name) or \
                   (self.audio_stream and stream.name() == self.audio_stream_name):
                    continue
                    
                stream_name = stream.name().lower()
                if participant_num in stream_name or f"p{participant_num}" in stream_name:
                    self.matched_streams.append(stream)
                    self.logger.log(f"Found matching stream: {stream.name()} ({stream.type()})")
            
            self.logger.log(f"Found {len(self.matched_streams)} streams to record")
            return len(self.matched_streams) > 0
                
        except Exception as e:
            self.logger.log(f"Stream scan error: {e}", level="error")
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
                    self._handle_click(0)  # Left button
                if r and not self.last_right:
                    self._handle_click(1)  # Right button
                if m and not self.last_middle:
                    self._handle_click(2)  # Middle button
                
                # Update previous states
                self.last_left, self.last_right, self.last_middle = l, r, m
                
                # Sleep for a short time
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            self.logger.log(f"Mouse polling error: {e}", level="error")
    
    def _handle_click(self, button_idx):
        """Process a mouse click for LSL streaming"""
        if not self.mouse_stream:
            return
            
        # Get cursor position and timestamp
        x, y = self._cursor()
        t = time.perf_counter() - self.start_time
        
        # Stream the click data to LSL
        self.mouse_stream.push_sample([t, float(x), float(y)])
        
        # Update click count
        self.click_count += 1
        
        button_name = "left" if button_idx == 0 else "right" if button_idx == 1 else "middle"
        self.logger.log(f"LSL mouse click: {button_name} @ {t:.3f}s ({x},{y})")
    
    def stream_audio(self):
        """Thread function to stream audio from Input 3/4 to LSL"""
        if not (self.audio_stream and self.streaming and self.audio_device_id is not None):
            self.logger.log("Cannot start audio streaming - missing requirements", level="warning")
            return
            
        self.audio_running = True
        audio_chunks_sent = 0
        device = self.audio_device_id
        sample_rate = self.audio_sample_rate
        
        # Verify device settings
        try:
            sd.check_input_settings(
                device=device,
                samplerate=sample_rate,
                channels=AUDIO_CHANNELS,
                dtype="float32"
            )
        except Exception as e:
            self.logger.log(f"{sample_rate} Hz not accepted ({e}) → falling back to device default", level="warning")
            sample_rate = None
        
        # Audio callback function
        def audio_callback(indata, frames, time_info, status):
            if status and status.output_underflow:
                # Don't log buffer underflows as they're common
                pass
            elif status:
                self.logger.log(f"Audio status: {status}", level="warning")
                
            if not self.audio_running:
                raise sd.CallbackAbort
                
            # Push audio data to LSL
            try:
                self.audio_stream.push_chunk(indata.tolist())
                
                nonlocal audio_chunks_sent
                audio_chunks_sent += 1
                
                # Log progress occasionally
                if audio_chunks_sent % 500 == 0:  # Less frequent logging
                    self.logger.log(f"Audio: {audio_chunks_sent} chunks sent")
            except Exception as e:
                self.logger.log(f"Error pushing audio chunk: {e}", level="error")
        
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
                self.logger.log(f"Audio input stream opened (sr={sample_rate or 'device default'})")
                
                # Keep thread alive while streaming
                while self.audio_running and self.streaming:
                    sd.sleep(100)
                    
        except Exception as e:
            self.logger.log(f"Audio input stream error: {e}", level="error")
            
        self.logger.log("Audio streaming thread exited")
        self.audio_running = False
    
    def start_recorder(self, participant_info):
        """Start the LSL recorder in a separate thread"""
        if not LSL_AVAILABLE:
            return False
            
        if self.recording:
            self.logger.log("LSL recording already active")
            return True
        
        # Make sure we have streams to record
        if not self.matched_streams:
            if not self.scan_participant_streams(participant_info):
                self.logger.log("Cannot start recording - no streams found", level="warning")
                return False
            
        # Start recording in a thread
        threading.Thread(target=self._recording_thread, 
                        args=(participant_info,), 
                        daemon=True).start()
        return True
    
    def _recording_thread(self, participant_info):
        """Thread to handle LSL recording"""
        try:
            participant_id = participant_info.get("participant_id", "Unknown")
            
            # Prepare output file path
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = os.path.join(LSL_OUTPUT_DIR, f"{participant_id}_{timestamp}.xdf")
            
            # Create streamargs list with streams to record
            streamargs = []
            for stream in self.matched_streams:
                streamargs.append({
                    "name": stream.name(),
                    "type": stream.type(),
                    "source_id": stream.source_id()
                })
                self.logger.log(f"Recording stream: {stream.name()} ({stream.type()})")
            
            # Create recorder and start recording
            self.recorder = Recorder()
            self.recorder.start_recording(filename=output_filename, streamargs=streamargs)
            
            # Update status
            self.recording = True
            self.logger.log(f"LSL recording started to {output_filename}", level="success")
            
        except Exception as e:
            self.logger.log(f"Error starting LSL recording: {e}", level="error")
            self.recording = False
    
    def stop_recorder(self):
        """Stop the LSL recorder"""
        if not LSL_AVAILABLE or not self.recording:
            return
        
        if not self.recorder:
            self.logger.log("No active LSL recorder to stop")
            self.recording = False
            return
        
        try:
            # Give additional time for any remaining data to be written
            time.sleep(0.5)
            
            # Stop the recorder properly
            self.recorder.stop_recording()
            self.logger.log("LSL recording stopped", level="success")
            
        except Exception as e:
            self.logger.log(f"Error stopping LSL recording: {e}", level="error")
        finally:
            # Always clean up resources regardless of errors
            self.recording = False
            self.recorder = None
    
    def start_streaming(self, participant_info):
        """Start streaming mouse clicks and audio"""
        if not LSL_AVAILABLE:
            return False
            
        if self.streaming:
            self.logger.log("LSL streaming already active")
            return True
            
        # Create LSL streams if they don't exist
        if not self.mouse_stream and not self.audio_stream:
            if not self.create_streams(participant_info):
                self.logger.log("Failed to create LSL streams", level="error")
                return False
                
        # Start the system
        self.start_time = time.perf_counter()
        self.streaming = True
        self.click_count = 0
        
        # Start mouse polling thread
        self.mouse_thread = threading.Thread(target=self.poll_mouse, daemon=True)
        self.mouse_thread.start()
        
        # Start audio streaming if available
        if self.audio_stream and self.audio_device_id is not None:
            self.audio_thread = threading.Thread(target=self.stream_audio, daemon=True)
            self.audio_thread.start()
        
        self.logger.log(f"LSL streaming started", level="success")
        return True
    
    def stop_streaming(self):
        """Stop all streaming"""
        if not LSL_AVAILABLE or not self.streaming:
            return
            
        # Stop the system
        self.streaming = False
        self.audio_running = False
        
        # Wait for audio thread to finish
        if hasattr(self, 'audio_thread') and self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=1.0)
        
        # Calculate duration
        if hasattr(self, 'start_time') and self.start_time:
            duration = time.perf_counter() - self.start_time
            self.logger.log(f"LSL streaming stopped (duration: {duration:.1f}s)")
        else:
            self.logger.log("LSL streaming stopped")
        
        # Add slight delay to ensure streams are properly closed
        time.sleep(0.5)
    
    def stop_all(self):
        """Stop all LSL activity"""
        if not LSL_AVAILABLE:
            return
            
        # Stop recording first to ensure data is saved
        if self.recording:
            self.logger.log("Stopping LSL recording...")
            self.stop_recorder()
            # Add delay to ensure complete shutdown
            time.sleep(0.5)
        
        # Then stop streaming
        if self.streaming:
            self.logger.log("Stopping LSL streaming...")
            self.stop_streaming()
    
    def cleanup(self):
        """Clean up all resources"""
        self.stop_all()

def get_participant_info():
    """
    Retrieves the participant ID from the latest JSON file in the metadata directory
    Returns: participant_info dictionary or None if not found
    """
    if not os.path.exists(METADATA_DIR):
        print(f"Metadata directory does not exist: {METADATA_DIR}")
        return None
    
    # Find all JSON files in the directory
    json_files = glob.glob(os.path.join(METADATA_DIR, "*.json"))
    
    if not json_files:
        print(f"No JSON files found in {METADATA_DIR}")
        return None
    
    # Get the most recent file based on modification time
    latest_file = max(json_files, key=os.path.getmtime)
    
    try:
        # Read and parse the JSON file
        with open(latest_file, 'r') as f:
            metadata = json.load(f)
        
        participant_id = metadata.get("participant_id", "Unknown")
        
        # Extract numeric part if needed (e.g., extract 1 from "P001")
        match = re.search(r'P0*(\d+)', participant_id)
        if match:
            participant_num = int(match.group(1))
            metadata["participant_num"] = participant_num
        
        print(f"Found participant ID: {participant_id} from file: {os.path.basename(latest_file)}")
        return metadata
    
    except Exception as e:
        print(f"Error reading metadata file: {e}")
        return None

def parse_timestamp(timestamp_str):
    """Parse timestamp in format MM:SS.S to seconds."""
    if timestamp_str is None or timestamp_str == "":
        return None
        
    match = re.match(r'(\d+):(\d+\.\d+)', timestamp_str)
    if match:
        minutes, seconds = match.groups()
        return float(minutes) * 60 + float(seconds)
    return None

def load_tactile_times(participant_num):
    """Load tactile stimulus times from the design file."""
    try:
        # Load from design file
        design_file = os.path.join(EXPERIMENT_LOG_DIR, f"participant_{participant_num}_design.csv")
        print(f"Loading trial data from: {design_file}")
        
        if not os.path.exists(design_file):
            print(f"Design file not found: {design_file}")
            return []
        
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
                        time_sec = parse_timestamp(ts_str)
                        if time_sec is not None:
                            tactile_times.append(time_sec)
        
        print(f"Loaded {len(tactile_times)} tactile stimulus times")
        return tactile_times
    except Exception as e:
        print(f"Error loading tactile times: {e}")
        traceback.print_exc()
        return []

# ──────────────────────────────────────────────────────────────────────────────
#  Main Application Class
# ──────────────────────────────────────────────────────────────────────────────
class BreathingSpaceApp:
    """Main application integrating all functionality"""
    def __init__(self, root):
        self.root = root
        self.root.title("Unified Breathing Space Experiment")
        
        # Make window stay on top of other windows
        self.root.attributes("-topmost", True)
        
        # Set up window close handler
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Set up responsive window size (default is 90% of screen width/height)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = int(screen_width * 0.9)
        window_height = int(screen_height * 0.9)
        
        # Position window in center of screen
        x_position = (screen_width - window_width) // 2
        y_position = (screen_height - window_height) // 2
        
        # Set window size and position
        self.root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        
        # Initialize Logger first (for app-wide logging)
        self.logger = Logger()
        
        # Create component classes
        self.audio_player = AudioPlayer(self.logger)
        self.tone_generator = ToneGenerator(self.logger)
        self.lsl_manager = LSLManager(self.logger)
        
        # Set up state variables
        self.participant_info = None
        self.audio_file_path = None
        self.experiment_running = False
        self.start_time = None
        self.mouse_clicks = []
        self.tactile_times = []
        self.timeline_markers1 = []
        self.timeline_markers2 = []
        self.click_count = 0
        self.audio_duration = 0
        self.mouse_recentering_timers = []
        
        # Create UI
        self._create_ui(window_width, window_height)
        
        # Update logger with the text widget
        self.logger.set_widget(self.log_text)
        
        # Log startup
        self.logger.log("Application initialized", level="success")
        
        # Initialize - find participant, audio file, and tactile times
        self.root.after(500, self.initialize)
    
    def _create_ui(self, window_width, window_height):
        """Create the main UI framework"""
        # Create style for better appearance
        style = ttk.Style()
        style.configure("TFrame", padding=3)
        style.configure("TButton", padding=2)
        style.configure("TLabelframe", padding=5)
        
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Top section - Control panel
        self._create_control_panel(main_frame)
        
        # Create notebook with tabs for organization
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Create Experiment tab (timeline + click area)
        self._create_experiment_tab()
        
        # Create LSL tab (LSL streams and recording)
        self._create_lsl_tab()
        
        # Create Log tab
        self._create_log_tab()
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def _create_control_panel(self, parent):
        """Create the top control panel with participant info and playback controls"""
        # Control panel frame
        control_panel = ttk.Frame(parent)
        control_panel.pack(fill=tk.X, pady=5)
        
        # ---- Left side: Participant info ----
        info_frame = ttk.LabelFrame(control_panel, text="Participant Information")
        info_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5)
        
        # Participant info
        self.participant_label = ttk.Label(info_frame, text="Participant: Not loaded", font=("Arial", 10, "bold"))
        self.participant_label.grid(row=0, column=0, padx=5, pady=2, sticky=tk.W)
        
        # File info
        self.file_label = ttk.Label(info_frame, text="File: Not loaded")
        self.file_label.grid(row=1, column=0, padx=5, pady=2, sticky=tk.W)
        
        # Refresh button
        ttk.Button(
            info_frame, text="Refresh",
            command=self.refresh_participant
        ).grid(row=0, column=1, rowspan=2, padx=10, pady=5)
        
        # ---- Right side: Playback controls ----
        controls_frame = ttk.LabelFrame(control_panel, text="Playback Controls")
        controls_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        
        # Time input and progress display
        time_frame = ttk.Frame(controls_frame)
        time_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=2, sticky=tk.W)
        
        ttk.Label(time_frame, text="Start (mm:ss):").pack(side=tk.LEFT, padx=5)
        
        self.minutes_var = tk.StringVar(value="00")
        self.seconds_var = tk.StringVar(value="00")
        
        ttk.Entry(time_frame, textvariable=self.minutes_var, width=3).pack(side=tk.LEFT)
        ttk.Label(time_frame, text=":").pack(side=tk.LEFT)
        ttk.Entry(time_frame, textvariable=self.seconds_var, width=3).pack(side=tk.LEFT, padx=(0, 10))
        
        # Progress display
        self.progress_var = tk.StringVar(value="00:00 / 00:00")
        ttk.Label(time_frame, textvariable=self.progress_var).pack(side=tk.LEFT, padx=10)
        
        # Control buttons
        button_frame = ttk.Frame(controls_frame)
        button_frame.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W+tk.E)
        
        self.play_button = ttk.Button(button_frame, text="Start Experiment", 
                                     command=self.start_experiment, width=15)
        self.play_button.pack(side=tk.LEFT, padx=2)
        
        self.pause_button = ttk.Button(button_frame, text="Pause", 
                                      command=self.pause_resume, width=8)
        self.pause_button.pack(side=tk.LEFT, padx=2)
        self.pause_button.config(state=tk.DISABLED)
        
        self.stop_button = ttk.Button(button_frame, text="Stop", 
                                     command=self.stop_experiment, width=8)
        self.stop_button.pack(side=tk.LEFT, padx=2)
        self.stop_button.config(state=tk.DISABLED)
        
        # Settings frame
        settings_frame = ttk.Frame(controls_frame)
        settings_frame.grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W)
        
        # Buffer size
        ttk.Label(settings_frame, text="Buffer:").pack(side=tk.LEFT, padx=5)
        self.buffer_size = tk.StringVar(value="512")
        buffer_combo = ttk.Combobox(settings_frame, textvariable=self.buffer_size, 
                                 values=["128", "256", "512", "1024", "2048"], width=6, state="readonly")
        buffer_combo.pack(side=tk.LEFT, padx=2)
        
        # Test tone checkbox
        self.test_tones = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings_frame, text="Test tones", 
                      variable=self.test_tones).pack(side=tk.LEFT, padx=10)
        
        # LSL status display
        if LSL_AVAILABLE:
            lsl_label = ttk.Label(settings_frame, text="LSL:")
            lsl_label.pack(side=tk.LEFT, padx=(10, 2))
            
            self.lsl_status_var = tk.StringVar(value="Ready")
            ttk.Label(settings_frame, textvariable=self.lsl_status_var, 
                    font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        
        # Progress bar
        self.progress_bar_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            parent, 
            orient="horizontal", 
            mode="determinate",
            variable=self.progress_bar_var
        )
        self.progress_bar.pack(fill=tk.X, padx=5, pady=3)
    
    def _create_experiment_tab(self):
        """Create the experiment tab with timeline and click area"""
        experiment_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(experiment_frame, text="Experiment")
        
        # Timeline visualization
        timeline_frame = ttk.LabelFrame(experiment_frame, text="Experiment Timeline")
        timeline_frame.pack(fill=tk.X, pady=5)
        
        # First half timeline
        ttk.Label(timeline_frame, text="First Half:").pack(anchor=tk.W, pady=(0, 2))
        
        self.timeline_canvas1 = tk.Canvas(timeline_frame, height=50, bg="white")
        self.timeline_canvas1.pack(fill=tk.X, pady=2)
        
        # Second half timeline
        ttk.Label(timeline_frame, text="Second Half:").pack(anchor=tk.W, pady=(5, 2))
        
        self.timeline_canvas2 = tk.Canvas(timeline_frame, height=50, bg="white")
        self.timeline_canvas2.pack(fill=tk.X, pady=2)
        
        # Create timelines
        timeline_start_x = 50
        self.timeline_end_x = 0  # Will be set based on actual canvas width
        
        # First timeline
        self.timeline_y1 = 25
        
        # First timeline progress indicator
        self.progress_line1 = self.timeline_canvas1.create_line(
            timeline_start_x, self.timeline_y1 - 15, timeline_start_x, self.timeline_y1 + 15, 
            width=3, fill="green", state="hidden"
        )
        
        # Second timeline
        self.timeline_y2 = 25
        
        # Second timeline progress indicator
        self.progress_line2 = self.timeline_canvas2.create_line(
            timeline_start_x, self.timeline_y2 - 15, timeline_start_x, self.timeline_y2 + 15, 
            width=3, fill="green", state="hidden"
        )
        
        # Bind resize events
        self.timeline_canvas1.bind("<Configure>", lambda e: self.on_canvas_resize())
        self.timeline_canvas2.bind("<Configure>", lambda e: self.on_canvas_resize())
        
        # Click area
        click_frame = ttk.LabelFrame(experiment_frame, text="Mouse Click Area")
        click_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Canvas for visualizing mouse clicks
        self.click_canvas = tk.Canvas(click_frame, bg="lightyellow")
        self.click_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Add text to the click area
        self.click_text = self.click_canvas.create_text(
            400, 50,  # Position will be updated on resize
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
        self.click_canvas.bind("<Configure>", self.on_click_canvas_resize)
    
    def _create_lsl_tab(self):
        """Create the LSL tab for stream management and recording"""
        lsl_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(lsl_frame, text="LSL Streams")
        
        if not LSL_AVAILABLE:
            ttk.Label(lsl_frame, text="LSL libraries not available. Install pylsl and liesl to enable LSL functionality.", 
                    font=("Arial", 12), foreground="red").pack(expand=True, pady=50)
            return
            
        # Status section
        status_frame = ttk.LabelFrame(lsl_frame, text="LSL Status")
        status_frame.pack(fill=tk.X, pady=5)
        
        # Status grid
        for i, (label, var_name) in enumerate([
            ("Mouse Stream:", "mouse_stream_status"),
            ("Audio Stream:", "audio_stream_status"),
            ("Recording:", "recording_status"),
            ("Verified:", "verified_status")
        ]):
            ttk.Label(status_frame, text=label).grid(row=i, column=0, sticky=tk.W, padx=5, pady=3)
            var = tk.StringVar(value="Not active")
            setattr(self, var_name, var)
            ttk.Label(status_frame, textvariable=var).grid(row=i, column=1, sticky=tk.W, padx=5, pady=3)
        
        # Stream list section
        streams_frame = ttk.LabelFrame(lsl_frame, text="Available LSL Streams")
        streams_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Listbox for streams
        self.stream_listbox = tk.Listbox(streams_frame, height=8, font=("Consolas", 9))
        self.stream_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=5, padx=5)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(streams_frame, orient=tk.VERTICAL, command=self.stream_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
        self.stream_listbox.config(yscrollcommand=scrollbar.set)
        
        # Control buttons
        control_frame = ttk.Frame(lsl_frame)
        control_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(control_frame, text="Scan Streams", 
                  command=self.scan_lsl_streams).pack(side=tk.LEFT, padx=5)
                  
        ttk.Button(control_frame, text="Test Tone", 
                  command=self.test_tone).pack(side=tk.LEFT, padx=5)
        
        # LSL manual controls
        manual_frame = ttk.LabelFrame(lsl_frame, text="Manual LSL Controls")
        manual_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(manual_frame, text="Start Streaming", 
                  command=self.start_lsl_streaming).pack(side=tk.LEFT, padx=5, pady=5)
                  
        ttk.Button(manual_frame, text="Start Recording", 
                  command=self.start_lsl_recording).pack(side=tk.LEFT, padx=5, pady=5)
                  
        ttk.Button(manual_frame, text="Stop All LSL", 
                  command=self.stop_lsl_all).pack(side=tk.LEFT, padx=5, pady=5)
    
    def _create_log_tab(self):
        """Create the log tab for messages"""
        log_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(log_frame, text="Log")
        
        # Log text widget
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, wrap=tk.WORD, 
                                               font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Configure tags for coloring
        self.log_text.tag_configure("error", foreground="red")
        self.log_text.tag_configure("warning", foreground="orange")
        self.log_text.tag_configure("success", foreground="green")
        
        # Disable editing but allow selection/copying
        self.log_text.config(state=tk.DISABLED)
        
        # Control buttons
        button_frame = ttk.Frame(log_frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(button_frame, text="Clear Log", 
                  command=self.clear_log).pack(side=tk.RIGHT, padx=5)
    
    def clear_log(self):
        """Clear the log text widget"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
    
    def test_tone(self):
        """Play a test tone"""
        self.tone_generator.play_tone()
        self.logger.log("Test tone played")
    
    def initialize(self):
        """Initialize application state - load participant and audio file"""
        # Try to find participant information
        self.refresh_participant()
        
        # Select the Experiment tab
        self.notebook.select(0)
    
    def refresh_participant(self):
        """Refresh participant information from metadata"""
        self.logger.log("Refreshing participant information...")
        
        # Find participant information from metadata
        self.participant_info = get_participant_info()
        
        if self.participant_info:
            # Update UI
            participant_id = self.participant_info["participant_id"]
            participant_num = self.participant_info.get("participant_num", 0)
            
            self.participant_label.config(text=f"Participant: {participant_id}")
            self.status_var.set(f"Participant {participant_id} loaded")
            
            # Look for corresponding audio file
            audio_filename = f"participant_{participant_num}_combined.wav"
            audio_path = os.path.join(EXPERIMENT_AUDIO_DIR, audio_filename)
            
            if os.path.exists(audio_path):
                self.audio_file_path = audio_path
                self.file_label.config(text=f"File: {audio_filename}")
                self.logger.log(f"Found audio file: {audio_filename}")
                
                # Get audio duration for timeline
                try:
                    info = sf.info(audio_path)
                    self.audio_duration = info.duration
                    self.logger.log(f"Audio duration: {self.audio_duration:.2f} seconds")
                except Exception as e:
                    self.logger.log(f"Error getting audio info: {e}", level="error")
            else:
                self.audio_file_path = None
                self.file_label.config(text="File: Not found")
                self.logger.log(f"Audio file not found: {audio_path}", level="warning")
                messagebox.showwarning(
                    "File Not Found", 
                    f"Audio file not found: {audio_filename}\nPlease check the audio directory."
                )
            
            # Load tactile times
            self.tactile_times = load_tactile_times(participant_num)
            
            # Update timeline with duration and tactile times
            self.update_timeline_with_duration()
            
            # Update LSL status if available
            if LSL_AVAILABLE:
                self.update_lsl_status()
                
            # Scan for streams if LSL is available
            if LSL_AVAILABLE:
                self.scan_lsl_streams()
        else:
            self.participant_label.config(text="Participant: Not found")
            self.file_label.config(text="File: Not loaded")
            self.status_var.set("No participant metadata found")
            self.logger.log("No participant metadata found", level="warning")
            messagebox.showwarning(
                "No Participant", 
                "No participant metadata found. Please run the participant selector first."
            )
            
    def update_lsl_status(self):
        """Update LSL status displays"""
        if not LSL_AVAILABLE:
            return
            
        # Update stream status
        self.mouse_stream_status.set("Not active" if self.lsl_manager.mouse_stream is None else 
                                   f"Active: {self.lsl_manager.mouse_stream_name}")
        
        self.audio_stream_status.set("Not active" if self.lsl_manager.audio_stream is None else 
                                   f"Active: {self.lsl_manager.audio_stream_name}")
        
        # Update recording status
        self.recording_status.set("Active" if self.lsl_manager.recording else "Not active")
        
        # Update verification status
        self.verified_status.set("Verified" if self.lsl_manager.verified else "Not verified")
        
        # Update main status
        if self.lsl_manager.streaming and self.lsl_manager.recording:
            self.lsl_status_var.set("Streaming & Recording")
        elif self.lsl_manager.streaming:
            self.lsl_status_var.set("Streaming")
        elif self.lsl_manager.recording:
            self.lsl_status_var.set("Recording")
        else:
            self.lsl_status_var.set("Ready")
    
    def scan_lsl_streams(self):
        """Scan for available LSL streams"""
        if not LSL_AVAILABLE or not self.participant_info:
            return
            
        # Clear the listbox
        self.stream_listbox.delete(0, tk.END)
        
        try:
            # Scan for all streams
            all_streams = resolve_streams(wait_time=1.0)
            
            if not all_streams:
                self.logger.log("No LSL streams found during scan", level="warning")
                self.stream_listbox.insert(tk.END, "No streams found")
                return
            
            # Get participant number
            participant_id = self.participant_info.get("participant_id", "Unknown")
            participant_num = str(self.participant_info.get("participant_num", ""))
            
            # Add streams to listbox
            for i, stream in enumerate(all_streams):
                stream_name = stream.name()
                stream_type = stream.type()
                
                # Check if it's one of our own streams
                is_own_mouse = (self.lsl_manager.mouse_stream and 
                             stream_name == self.lsl_manager.mouse_stream_name)
                is_own_audio = (self.lsl_manager.audio_stream and 
                             stream_name == self.lsl_manager.audio_stream_name)
                
                # Check if it's related to this participant
                is_matched = (participant_num in stream_name.lower() or 
                           f"p{participant_num}" in stream_name.lower())
                
                # Create display text with annotation
                display_text = f"{stream_name} ({stream_type})"
                if is_own_mouse:
                    display_text += " - OUR MOUSE STREAM"
                elif is_own_audio:
                    display_text += " - OUR AUDIO STREAM"
                elif is_matched:
                    display_text += " - MATCHED"
                
                self.stream_listbox.insert(tk.END, display_text)
                
                # Add to matched streams for recording
                if is_own_mouse or is_own_audio or is_matched:
                    if stream not in self.lsl_manager.matched_streams:
                        self.lsl_manager.matched_streams.append(stream)
            
            self.logger.log(f"Found {len(all_streams)} LSL streams, {len(self.lsl_manager.matched_streams)} matched")
            
        except Exception as e:
            self.logger.log(f"Error scanning LSL streams: {e}", level="error")
    
    def get_start_time(self):
        """Get the start time in seconds from the minute/second inputs"""
        try:
            minutes = int(self.minutes_var.get())
            seconds = int(self.seconds_var.get())
            return minutes * 60 + seconds
        except ValueError:
            return 0
    
    def on_canvas_resize(self):
        """Handle timeline canvas resize events"""
        if hasattr(self, 'audio_duration') and self.audio_duration > 0:
            self.update_timeline_with_duration()
    
    def on_click_canvas_resize(self, event):
        """Handle click canvas resize events"""
        # Update text position to center
        if hasattr(self, 'click_text'):
            canvas_width = event.width
            canvas_height = event.height
            self.click_canvas.coords(self.click_text, canvas_width // 2, 50)
    
    def update_timeline_with_duration(self):
        """Update both timelines with markers based on audio duration"""
        if not hasattr(self, 'audio_duration') or self.audio_duration <= 0:
            return
            
        # Clear existing time markers
        for canvas in [self.timeline_canvas1, self.timeline_canvas2]:
            canvas.delete("all")  # Clear everything and redraw
            
        # Get current canvas widths
        timeline_start_x = 50
        
        # Calculate dynamic end position based on current canvas width
        self.timeline_end_x1 = self.timeline_canvas1.winfo_width() - 60
        self.timeline_end_x2 = self.timeline_canvas2.winfo_width() - 60
        
        if self.timeline_end_x1 < 100:  # Canvas not fully rendered yet
            # Use fallback value and schedule another update
            self.timeline_end_x1 = 400
            self.timeline_end_x2 = 400
            self.root.after(100, self.update_timeline_with_duration)
        
        # First timeline base line
        self.timeline_canvas1.create_line(timeline_start_x, self.timeline_y1, 
                                        self.timeline_end_x1, self.timeline_y1, width=2)
        
        # Second timeline base line
        self.timeline_canvas2.create_line(timeline_start_x, self.timeline_y2, 
                                        self.timeline_end_x2, self.timeline_y2, width=2)
        
        # Add timestamp at start
        self.timeline_canvas1.create_text(timeline_start_x - 30, self.timeline_y1, 
                                       text="0:00", font=("Arial", 8))
        
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
        timeline_width1 = self.timeline_end_x1 - timeline_start_x
        for sec in range(0, int(halfway_time) + interval, interval):
            if sec > halfway_time:
                break
                
            x_pos = timeline_start_x + (sec / halfway_time) * timeline_width1
            
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
        timeline_width2 = self.timeline_end_x2 - timeline_start_x
        for sec in range(0, int(halfway_time) + interval, interval):
            if sec > halfway_time:
                break
                
            x_pos = timeline_start_x + (sec / halfway_time) * timeline_width2
            
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
        
        # Recreate progress indicators
        self.progress_line1 = self.timeline_canvas1.create_line(
            timeline_start_x, self.timeline_y1 - 15, timeline_start_x, self.timeline_y1 + 15, 
            width=3, fill="green", state="hidden"
        )
        
        self.progress_line2 = self.timeline_canvas2.create_line(
            timeline_start_x, self.timeline_y2 - 15, timeline_start_x, self.timeline_y2 + 15, 
            width=3, fill="green", state="hidden"
        )
        
        # Add tactile event markers
        for t_time in self.tactile_times:
            self.add_timeline_marker(t_time, "blue")
    
    def add_timeline_marker(self, time_sec, color):
        """Add a marker to the timeline at the specified time."""
        if not hasattr(self, 'audio_duration') or self.audio_duration <= 0:
            return
            
        timeline_start_x = 50
        
        # Calculate the halfway point of the audio
        halfway_time = self.audio_duration / 2
        
        # Determine which timeline to use based on time
        if time_sec <= halfway_time:
            # First half timeline
            timeline_width = self.timeline_end_x1 - timeline_start_x if hasattr(self, 'timeline_end_x1') else 400
            x_pos = timeline_start_x + (time_sec / halfway_time) * timeline_width
            
            # Create marker
            marker = self.timeline_canvas1.create_oval(
                x_pos-4, self.timeline_y1-4, x_pos+4, self.timeline_y1+4, 
                fill=color, outline="black", width=1
            )
            self.timeline_markers1.append(marker)
        else:
            # Second half timeline - adjust position to start from beginning of second timeline
            timeline_width = self.timeline_end_x2 - timeline_start_x if hasattr(self, 'timeline_end_x2') else 400
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
        if not hasattr(self, 'audio_duration') or self.audio_duration <= 0:
            return
            
        # Calculate the halfway point of the audio
        halfway_time = self.audio_duration / 2
        
        # Update the appropriate timeline progress line
        timeline_start_x = 50
        
        if elapsed_time <= halfway_time:
            # First half timeline
            timeline_width = self.timeline_end_x1 - timeline_start_x if hasattr(self, 'timeline_end_x1') else 400
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
            timeline_width1 = self.timeline_end_x1 - timeline_start_x if hasattr(self, 'timeline_end_x1') else 400
            self.timeline_canvas1.coords(
                self.progress_line1, 
                timeline_width1 + timeline_start_x, self.timeline_y1 - 15, 
                timeline_width1 + timeline_start_x, self.timeline_y1 + 15
            )
            
            # Update second timeline progress line
            timeline_width2 = self.timeline_end_x2 - timeline_start_x if hasattr(self, 'timeline_end_x2') else 400
            adjusted_time = elapsed_time - halfway_time
            x_pos = timeline_start_x + (adjusted_time / halfway_time) * timeline_width2
            
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
    
    def center_mouse_in_click_area(self):
        """Center the mouse cursor in the click area."""
        if not hasattr(self, 'click_canvas') or not self.click_canvas.winfo_exists():
            return
            
        try:
            # Get canvas dimensions and position
            canvas_width = self.click_canvas.winfo_width()
            canvas_height = self.click_canvas.winfo_height()
            
            # Get canvas position on screen
            canvas_x = self.click_canvas.winfo_rootx()
            canvas_y = self.click_canvas.winfo_rooty()
            
            # Calculate center position
            center_x = canvas_x + canvas_width // 2
            center_y = canvas_y + canvas_height // 2
            
            # Move mouse to center
            ctypes.windll.user32.SetCursorPos(center_x, center_y)
            self.logger.log(f"Mouse centered at {center_x}, {center_y}")
        except Exception as e:
            self.logger.log(f"Error centering mouse: {e}", level="warning")
    
    def setup_mouse_recentering(self):
        """Set up timers to recenter mouse 1 second before each tactile stimulus."""
        # Cancel any existing timers
        for timer_id in self.mouse_recentering_timers:
            self.root.after_cancel(timer_id)
        self.mouse_recentering_timers = []
        
        if not self.tactile_times or not self.start_time:
            return
            
        current_time = time.perf_counter() - self.start_time
        
        for t_time in self.tactile_times:
            # Calculate time until 1 second before tactile stimulus
            time_until_recenter = (t_time - 1.0) - current_time
            
            if time_until_recenter > 0:
                # Schedule mouse recentering
                timer_id = self.root.after(
                    int(time_until_recenter * 1000),  # Convert to milliseconds
                    self.center_mouse_in_click_area
                )
                
                self.mouse_recentering_timers.append(timer_id)
                self.logger.log(f"Scheduled mouse recentering 1s before tactile at {t_time:.2f}s")
    
    def on_mouse_click(self, event):
        """Handle mouse click events during the experiment."""
        if not self.experiment_running or not hasattr(self, 'start_time') or self.start_time is None:
            return
            
        # Calculate time since experiment start
        current_time = time.perf_counter() - self.start_time
        
        # Add to mouse clicks list
        self.mouse_clicks.append({
            "time": current_time,
            "timestamp": dt.datetime.now().isoformat(),
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
        
        # Play tone if clicked during experiment
        self.tone_generator.play_tone()
        
        self.logger.log(f"Mouse click at {current_time:.3f} seconds")
    
    def start_experiment(self):
        """Start the experiment."""
        # Check if we have everything we need
        if not self.participant_info:
            messagebox.showerror("Error", "No participant information found.")
            return
            
        if not self.audio_file_path:
            messagebox.showerror("Error", "No audio file found for this participant.")
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
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Initializing experiment...")
        
        # Reset click counter
        self.click_count = 0
        self.mouse_clicks = []
        self.click_canvas.itemconfig(self.click_counter_text, text=f"Clicks: 0")
        
        # Clear timeline and add tactile markers
        self.clear_timeline()
        for t_time in self.tactile_times:
            self.add_timeline_marker(t_time, "blue")
        
        # Cancel any existing progress updates
        if hasattr(self, 'update_progress_id') and self.update_progress_id is not None:
            self.root.after_cancel(self.update_progress_id)
            self.update_progress_id = None
        
        # Start experiment in a separate thread
        threading.Thread(target=self._experiment_thread, 
                        args=(buffer_size, start_time), 
                        daemon=True).start()
    
    def _experiment_thread(self, buffer_size, start_time):
        """Thread function to handle experiment execution."""
        try:
            # Start LSL components if available
            lsl_started = False
            if LSL_AVAILABLE:
                self.root.after(0, lambda: self.lsl_status_var.set("Starting LSL..."))
                lsl_started = self.lsl_manager.start_streaming(self.participant_info)
                
                if lsl_started:
                    self.logger.log("LSL streaming started successfully")
                    
                    # Scan for existing streams before starting recording
                    time.sleep(0.5)  # Brief delay to allow streams to register
                    self.lsl_manager.scan_participant_streams(self.participant_info)
                    
                    # Start recording
                    if self.lsl_manager.start_recorder(self.participant_info):
                        self.logger.log("LSL recording started successfully")
                    else:
                        self.logger.log("LSL recording failed to start", level="warning")
                    
                    # Update LSL status
                    self.root.after(0, self.update_lsl_status)
                else:
                    self.logger.log("LSL streaming failed to start", level="warning")
            
            # Update status
            self.root.after(0, lambda: self.status_var.set(f"Loading: {os.path.basename(self.audio_file_path)}"))
            
            # Load the audio file
            if not self.audio_player.load_file(self.audio_file_path):
                self.root.after(0, lambda: self.status_var.set("Error loading audio file"))
                self.root.after(0, lambda: self.reset_ui())
                return
            
            # Prepare for playback
            if not self.audio_player.prepare_playback(buffer_size=buffer_size, test_tones=self.test_tones.get()):
                self.root.after(0, lambda: self.status_var.set("Error preparing audio playback"))
                self.root.after(0, lambda: self.reset_ui())
                return
            
            # Enable UI controls
            self.root.after(0, lambda: self.pause_button.config(state=tk.NORMAL, text="Pause"))
            self.root.after(0, lambda: self.stop_button.config(state=tk.NORMAL))
            
            # Record start time for experiment timeline
            self.start_time = time.perf_counter()
            self.experiment_running = True
            
            # Setup mouse recentering before tactile stimuli
            self.setup_mouse_recentering()
            
            # Start playback
            if not self.audio_player.start_playback(buffer_size=buffer_size, start_time=start_time):
                self.root.after(0, lambda: self.status_var.set("Error starting playback"))
                self.root.after(0, lambda: self.reset_ui())
                return
            
            # Start progress updates
            self.root.after(0, self.update_progress)
            self.root.after(0, lambda: self.status_var.set(f"Playing: {os.path.basename(self.audio_file_path)}"))
            
        except Exception as e:
            self.logger.log(f"Error in experiment thread: {e}", level="error")
            self.root.after(0, lambda: self.reset_ui())
            self.root.after(0, lambda: self.status_var.set("Error occurred during experiment setup"))
    
    def pause_resume(self):
        """Pause or resume audio playback."""
        if not self.audio_player.is_playing:
            self.logger.log("Nothing playing to pause/resume")
            return
            
        if self.audio_player.is_paused:
            # Resume playback
            if self.audio_player.resume_playback():
                self.pause_button.config(text="Pause")
                self.status_var.set(f"Playing: {os.path.basename(self.audio_file_path)}")
                
                # Setup mouse recentering again
                self.setup_mouse_recentering()
            else:
                self.stop_experiment()
        else:
            # Pause playback
            if self.audio_player.pause_playback():
                self.pause_button.config(text="Resume")
                self.status_var.set(f"Paused: {os.path.basename(self.audio_file_path)}")
                
                # Cancel mouse recentering timers
                for timer_id in self.mouse_recentering_timers:
                    self.root.after_cancel(timer_id)
                self.mouse_recentering_timers = []
            else:
                self.stop_experiment()
    
    def stop_experiment(self):
        """Stop the experiment completely."""
        self.experiment_running = False
        
        # Cancel mouse recentering timers
        for timer_id in self.mouse_recentering_timers:
            self.root.after_cancel(timer_id)
        self.mouse_recentering_timers = []
        
        # Stop audio playback
        if self.audio_player.stop_playback():
            # Full reset to ensure clean state
            self.audio_player.reset()
            
            # Save click data
            self.save_click_data()
            
            # Stop LSL components if active
            if LSL_AVAILABLE and (self.lsl_manager.streaming or self.lsl_manager.recording):
                self.stop_lsl_all()
            
            # Update UI
            self.reset_ui()
            self.status_var.set("Experiment stopped")
            self.logger.log("Experiment stopped", level="success")
        
    def reset_ui(self):
        """Reset UI elements after playback finishes."""
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED, text="Pause")
        self.stop_button.config(state=tk.DISABLED)
        
        # Cancel progress updates
        if hasattr(self, 'update_progress_id') and self.update_progress_id is not None:
            self.root.after_cancel(self.update_progress_id)
            self.update_progress_id = None
        
        # Reset experiment running flag
        self.experiment_running = False
    
    def update_progress(self):
        """Update progress display and timeline."""
        if not self.audio_player.is_playing:
            self.reset_ui()
            self.progress_var.set("00:00 / 00:00")
            self.progress_bar_var.set(0)
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
            
            # Schedule next update - don't accumulate if there are delays
            if hasattr(self, 'update_progress_id') and self.update_progress_id is not None:
                self.root.after_cancel(self.update_progress_id)
            
            # Check again if still playing before scheduling next update
            if self.audio_player.is_playing:
                self.update_progress_id = self.root.after(200, self.update_progress)
            else:
                self.reset_ui()
                self.status_var.set("Playback complete")
                
                # Save click data when playback completes naturally
                self.save_click_data()
                
                # Stop LSL if auto-started
                if LSL_AVAILABLE and self.lsl_manager.streaming:
                    self.logger.log("Stopping LSL streaming and recording...")
                    self.stop_lsl_all()
                
        except Exception as e:
            self.logger.log(f"Error updating progress: {e}", level="error")
            # Schedule recovery update
            self.update_progress_id = self.root.after(500, self.update_progress)
    
    def save_click_data(self):
        """Save the mouse click data to CSV."""
        if not self.mouse_clicks:
            self.logger.log("No click data to save")
            return
            
        try:
            # Create timestamp for the filename
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Use participant ID if available
            participant_id = self.participant_info["participant_id"] if self.participant_info else "unknown"
            participant_num = self.participant_info.get("participant_num", 0)
            
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
            
            self.logger.log(f"Saved click data to {filename}", level="success")
            self.status_var.set(f"Click data saved")
            
        except Exception as e:
            self.logger.log(f"Error saving click data: {e}", level="error")
            self.status_var.set(f"Error saving click data")
    
    def start_lsl_streaming(self):
        """Manually start LSL streaming."""
        if not LSL_AVAILABLE:
            messagebox.showinfo("LSL Not Available", "LSL libraries are not installed.")
            return
            
        if not self.participant_info:
            messagebox.showinfo("No Participant", "Please load a participant first.")
            return
            
        # Start streaming
        if self.lsl_manager.start_streaming(self.participant_info):
            self.logger.log("LSL streaming started manually", level="success")
            self.update_lsl_status()
        else:
            self.logger.log("Failed to start LSL streaming", level="error")
    
    def start_lsl_recording(self):
        """Manually start LSL recording."""
        if not LSL_AVAILABLE:
            messagebox.showinfo("LSL Not Available", "LSL libraries are not installed.")
            return
            
        if not self.participant_info:
            messagebox.showinfo("No Participant", "Please load a participant first.")
            return
            
        # Make sure we have streams to record
        if not self.lsl_manager.streaming and not self.lsl_manager.matched_streams:
            if not self.lsl_manager.scan_participant_streams(self.participant_info):
                if not messagebox.askyesno("No Streams", "No matching streams found. Start recording anyway?"):
                    return
        
        # Start recording
        if self.lsl_manager.start_recorder(self.participant_info):
            self.logger.log("LSL recording started manually", level="success")
            self.update_lsl_status()
        else:
            self.logger.log("Failed to start LSL recording", level="error")
    
    def stop_lsl_all(self):
        """Stop all LSL activity."""
        if not LSL_AVAILABLE:
            return
            
        self.lsl_manager.stop_all()
        self.update_lsl_status()
        self.logger.log("All LSL activity stopped", level="success")
    
    def on_closing(self):
        """Handle window closing event."""
        self.logger.log("Application closing...")
        
        # Check if experiment is running
        if self.experiment_running:
            if not messagebox.askyesno("Close Application", 
                                     "Experiment is running. Are you sure you want to quit?"):
                return
        
        # Cancel any scheduled updates
        if hasattr(self, 'update_progress_id') and self.update_progress_id is not None:
            try:
                self.root.after_cancel(self.update_progress_id)
            except:
                pass
            self.update_progress_id = None
        
        # Cancel mouse recentering timers
        for timer_id in self.mouse_recentering_timers:
            try:
                self.root.after_cancel(timer_id)
            except:
                pass
        self.mouse_recentering_timers = []
        
        # Stop audio playback
        try:
            self.audio_player.reset()
        except:
            pass
        
        # Stop LSL components
        if LSL_AVAILABLE:
            try:
                self.lsl_manager.cleanup()
            except:
                pass
                
        # Clean up tone generator
        try:
            self.tone_generator.cleanup()
        except:
            pass
        
        # Force stop any remaining audio output
        try:
            sd.stop()
        except:
            pass
        
        # Destroy the window and exit
        self.logger.log("Application closed.")
        self.root.destroy()