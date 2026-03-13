#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PPS Minimal Experiment Runner

This is a streamlined version focusing on core functionality:
- Loading and playing the two audio files
- Displaying the correct timeline length
- Showing the tactile stimulus times
- Capturing and displaying mouse clicks

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

# Configuration
BASE_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace"
EXPERIMENT_AUDIO_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentAudio")
EXPERIMENT_LOG_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentLog")
RESULTS_DIR = os.path.join(BASE_DIR, "Level2_RunExperiment", "Results")

# Ensure results directory exists
os.makedirs(RESULTS_DIR, exist_ok=True)

class MinimalExperimentRunner:
    def __init__(self):
        # Initialize variables
        self.participant_id = None
        self.available_participants = []
        self.experiment_running = False
        self.start_time = None
        self.mouse_clicks = []
        self.tactile_times = []
        self.timeline_markers1 = []
        self.timeline_markers2 = []
        self.click_count = 0
        self.audio_duration = 0  # Will be set based on loaded audio
        
        # Flag to control audio playback
        self.stop_audio = False
        
        # Scan for available participants
        self.scan_available_participants()
        
        # Create GUI
        self.create_gui()

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
    
    def create_gui(self):
        """Create the GUI."""
        self.root = tk.Tk()
        self.root.title("PPS Experiment Runner")
        
        # Set protocol for window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Set window size to 80% of screen
        window_width = int(screen_width * 0.8)
        window_height = int(screen_height * 0.8)
        self.root.geometry(f"{window_width}x{window_height}")
        
        # Main frame with padding
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        ttk.Label(main_frame, text="PPS Experiment Runner", font=("Arial", 16, "bold")).pack(pady=10)
        
        # Control buttons frame at the TOP
        control_frame = ttk.LabelFrame(main_frame, text="Experiment Control", padding=10)
        control_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Start button
        self.start_button = ttk.Button(
            control_frame, text="START EXPERIMENT",
            command=self.start_experiment, width=20
        )
        self.start_button.pack(side=tk.LEFT, padx=10, pady=5)
        
        # Stop button
        self.stop_button = ttk.Button(
            control_frame, text="STOP",
            command=self.stop_experiment, width=10,
            state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=10, pady=5)
        
        # Quit button
        ttk.Button(
            control_frame, text="QUIT",
            command=self.on_close, width=10
        ).pack(side=tk.RIGHT, padx=10, pady=5)
        
        # Participant selection frame
        participant_frame = ttk.LabelFrame(main_frame, text="Participant Selection", padding=10)
        participant_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Horizontal frame for participant selection
        selection_frame = ttk.Frame(participant_frame)
        selection_frame.pack(fill=tk.X, pady=5)
        
        # Participant ID selection
        ttk.Label(selection_frame, text="Participant ID:").pack(side=tk.LEFT, padx=5)
        
        self.participant_var = tk.StringVar()
        if self.available_participants:
            self.participant_var.set(str(self.available_participants[0]))
        
        participant_dropdown = ttk.Combobox(
            selection_frame, 
            textvariable=self.participant_var,
            values=[str(p) for p in self.available_participants],
            width=5
        )
        participant_dropdown.pack(side=tk.LEFT, padx=5)
        
        # Refresh button
        ttk.Button(
            selection_frame, 
            text="Refresh List",
            command=self.refresh_participants
        ).pack(side=tk.LEFT, padx=10)
        
        # Status display
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=5)
        
        self.status_var = tk.StringVar(value="Select a participant and press START EXPERIMENT")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, 
                                     font=("Arial", 11, "bold"))
        self.status_label.pack(pady=5)
        
        # Progress bar
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            main_frame, 
            orient="horizontal", 
            length=window_width-50,
            mode="determinate",
            variable=self.progress_var
        )
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)
        
        # Split Timeline frame - two timelines for better visualization
        timeline_frame = ttk.LabelFrame(main_frame, text="Experiment Timeline", padding=10)
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
        
        # Initialize markers list for each timeline
        self.timeline_markers1 = []
        self.timeline_markers2 = []
        
        # Mouse click visualization area
        click_frame = ttk.LabelFrame(main_frame, text="Mouse Click Area", padding=10)
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
                self.root.destroy()
        else:
            self.root.destroy()
    
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
            "y": event.y
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
        
        print(f"Mouse click at {current_time:.3f} seconds")
    
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
            
            # Only update progress line if in the first half
            if time_sec <= halfway_time:
                self.timeline_canvas1.coords(
                    self.progress_line1, 
                    x_pos, self.timeline_y1-15, 
                    x_pos, self.timeline_y1+15
                )
                self.timeline_canvas1.itemconfig(self.progress_line1, state="normal")
                
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
            
            # Update progress line on second timeline
            self.timeline_canvas2.coords(
                self.progress_line2, 
                x_pos, self.timeline_y2-15, 
                x_pos, self.timeline_y2+15
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
    
    def update_progress(self, elapsed_time):
        """Update progress bar and timeline progress lines."""
        if self.audio_duration <= 0:
            return
            
        # Update progress bar - based on full duration
        progress_percent = min(100, (elapsed_time / self.audio_duration) * 100)
        self.progress_var.set(progress_percent)
        
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
    
    def parse_timestamp(self, timestamp_str):
        """Parse timestamp in format MM:SS.S to seconds."""
        if pd.isna(timestamp_str):
            return None
            
        match = re.match(r'(\d+):(\d+\.\d+)', timestamp_str)
        if match:
            minutes, seconds = match.groups()
            return float(minutes) * 60 + float(seconds)
        return None
    
    def load_tactile_times(self, participant_id):
        """Load tactile stimulus times from the design file."""
        try:
            # Load from design file
            design_file = os.path.join(EXPERIMENT_LOG_DIR, f"participant_{participant_id}_design.csv")
            print(f"Loading trial data from: {design_file}")
            
            design_df = pd.read_csv(design_file)
            print(f"Loaded design with {len(design_df)} rows")
            
            # Filter for trials with tactile stimuli (exclude catch trials)
            tactile_trials = design_df[design_df['trial_type'] != 'catch']
            
            # Extract tactile stimulus times
            tactile_times = []
            for _, row in tactile_trials.iterrows():
                if 'tactile_stimulus_timestamp' in row and pd.notna(row['tactile_stimulus_timestamp']):
                    ts_str = row['tactile_stimulus_timestamp']
                    time_sec = self.parse_timestamp(ts_str)
                    if time_sec is not None:
                        tactile_times.append(time_sec)
            
            print(f"Loaded {len(tactile_times)} tactile stimulus times")
            return tactile_times
        except Exception as e:
            print(f"Error loading tactile times: {e}")
            traceback.print_exc()
            return []
    
    def start_experiment(self):
        """Start the experiment."""
        # Get participant ID
        try:
            self.participant_id = int(self.participant_var.get())
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Please select a valid participant ID")
            return
        
        print(f"\n===== STARTING EXPERIMENT FOR PARTICIPANT {self.participant_id} =====")
        
        # Reset state
        self.stop_audio = False
        self.mouse_clicks = []
        self.click_count = 0
        self.experiment_running = True
        self.clear_timeline()
        
        # Update status
        self.status_var.set(f"Starting experiment for participant {self.participant_id}...")
        
        # Update UI
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.click_canvas.itemconfig(self.click_counter_text, text=f"Clicks: 0")
        
        # Run experiment in background thread
        threading.Thread(target=self.run_experiment, daemon=True).start()
    
    def stop_experiment(self):
        """Stop the experiment."""
        if not self.experiment_running:
            return
            
        self.stop_audio = True
        sd.stop()
        print("Experiment stopped manually")
        
        # Update status
        self.status_var.set("Experiment stopped")
        
        # Update UI
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        
        # Save click data
        self.save_click_data()
    
    def run_experiment(self):
        """Run the experiment (play audio files and track responses)."""
        try:
            # Get audio file paths
            looming_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{self.participant_id}_design_looming.wav")
            tactile_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{self.participant_id}_design_tactile.wav")
            
            print(f"Using audio files:")
            print(f"- Looming: {looming_file}")
            print(f"- Tactile: {tactile_file}")
            
            # Verify files exist
            if not os.path.exists(looming_file) or not os.path.exists(tactile_file):
                self.status_var.set(f"Error: Audio files not found")
                print(f"ERROR: Missing audio files")
                self.experiment_running = False
                self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
                return
            
            # Load tactile stimulus times
            self.tactile_times = self.load_tactile_times(self.participant_id)
            if not self.tactile_times:
                self.status_var.set(f"Warning: No tactile stimulus times found")
                print(f"WARNING: No tactile stimulus times found")
            
            # Load audio files to get duration
            looming_info = sf.info(looming_file)
            tactile_info = sf.info(tactile_file)
            
            # Set audio duration for timeline
            self.audio_duration = looming_info.duration
            print(f"Audio duration: {self.audio_duration:.2f} seconds")
            
            # Update timeline based on duration
            self.root.after(0, self.update_timeline_with_duration)
            
            # Ensure the progress indicators are hidden and positioned at the start
            self.timeline_canvas1.coords(
                self.progress_line1,
                50, self.timeline_y1-15,
                50, self.timeline_y1+15
            )
            self.timeline_canvas1.itemconfig(self.progress_line1, state="hidden")
            
            self.timeline_canvas2.coords(
                self.progress_line2,
                50, self.timeline_y2-15,
                50, self.timeline_y2+15
            )
            self.timeline_canvas2.itemconfig(self.progress_line2, state="hidden")
            
            # Load audio data
            print("Loading audio data...")
            looming_data, looming_sr = sf.read(looming_file)
            tactile_data, tactile_sr = sf.read(tactile_file)
            
            print(f"Audio loaded - Looming: {looming_data.shape}, Tactile: {tactile_data.shape}")
            
            # Convert stereo to mono if needed
            if len(looming_data.shape) > 1 and looming_data.shape[1] > 1:
                print("Converting looming audio from stereo to mono for playback")
                looming_data = np.mean(looming_data, axis=1)
            if len(tactile_data.shape) > 1 and tactile_data.shape[1] > 1:
                print("Converting tactile audio from stereo to mono for playback")
                tactile_data = np.mean(tactile_data, axis=1)
            
            # Set start time just before playback
            self.start_time = time.perf_counter()
            print(f"Starting audio playback at {datetime.datetime.now().strftime('%H:%M:%S.%f')}")
            
            # Initialize sounddevice audio streams directly
            print("Initializing audio streams...")
            
            # Check available devices
            devices = sd.query_devices()
            print(f"Available audio devices:")
            for i, dev in enumerate(devices):
                print(f"  {i}: {dev['name']}")
            
            # Use default output device if possible
            default_device = sd.query_devices(kind='output')
            print(f"Default output device: {default_device['name']}")
            
            # Play both audio files in separate threads
            def play_looming():
                try:
                    print("Starting looming audio playback...")
                    # Try to use device 0, fall back to default if it fails
                    try:
                        stream = sd.OutputStream(samplerate=looming_sr, channels=1, device=0)
                    except:
                        print("Falling back to default output device for looming")
                        stream = sd.OutputStream(samplerate=looming_sr, channels=1)
                        
                    stream.start()
                    
                    chunk_size = 1024
                    for i in range(0, len(looming_data), chunk_size):
                        if self.stop_audio:
                            print("Looming audio playback stopped")
                            break
                        chunk = looming_data[i:min(i+chunk_size, len(looming_data))]
                        stream.write(chunk.astype(np.float32))
                    
                    stream.stop()
                    stream.close()
                    print("Looming audio playback completed")
                except Exception as e:
                    print(f"Error playing looming audio: {e}")
                    traceback.print_exc()
            
            def play_tactile():
                try:
                    print("Starting tactile audio playback...")
                    # Try to use device 1, fall back to default if it fails
                    try:
                        stream = sd.OutputStream(samplerate=tactile_sr, channels=1, device=1)
                    except:
                        print("Falling back to default output device for tactile")
                        stream = sd.OutputStream(samplerate=tactile_sr, channels=1)
                        
                    stream.start()
                    
                    chunk_size = 1024
                    for i in range(0, len(tactile_data), chunk_size):
                        if self.stop_audio:
                            print("Tactile audio playback stopped")
                            break
                        chunk = tactile_data[i:min(i+chunk_size, len(tactile_data))]
                        stream.write(chunk.astype(np.float32))
                    
                    stream.stop()
                    stream.close()
                    print("Tactile audio playback completed")
                except Exception as e:
                    print(f"Error playing tactile audio: {e}")
                    traceback.print_exc()
            
            looming_thread = threading.Thread(target=play_looming, daemon=True)
            tactile_thread = threading.Thread(target=play_tactile, daemon=True)
            
            print("Starting audio threads...")
            looming_thread.start()
            tactile_thread.start()
            
            # Progress update loop
            self.status_var.set("Experiment running - click when you hear a tactile stimulus")
            
            # Update progress bar and timeline periodically
            update_interval = 0.1  # seconds
            end_time = self.start_time + self.audio_duration + 2.0  # Add a small buffer
            
            while (time.perf_counter() < end_time and 
                   not self.stop_audio and 
                   (looming_thread.is_alive() or tactile_thread.is_alive())):
                
                elapsed = time.perf_counter() - self.start_time
                
                # Update progress UI
                self.root.after(0, lambda t=elapsed: self.update_progress(t))
                
                # Update status with time
                minutes = int(elapsed // 60)
                seconds = int(elapsed % 60)
                total_minutes = int(self.audio_duration // 60)
                total_seconds = int(self.audio_duration % 60)
                
                self.root.after(0, lambda m=minutes, s=seconds, tm=total_minutes, ts=total_seconds: 
                              self.status_var.set(f"Experiment running - {m:02d}:{s:02d} / {tm:02d}:{ts:02d}"))
                
                time.sleep(update_interval)
            
            # Wait for threads to complete
            looming_thread.join(timeout=1.0)
            tactile_thread.join(timeout=1.0)
            
            if not self.stop_audio:
                print(f"Audio playback completed at {datetime.datetime.now().strftime('%H:%M:%S.%f')}")
                self.status_var.set("Experiment completed")
                
                # Save click data
                self.save_click_data()
            
        except Exception as e:
            print(f"ERROR during experiment: {e}")
            traceback.print_exc()
            self.status_var.set(f"Error: {str(e)}")
        
        finally:
            self.experiment_running = False
            
            # Update UI
            self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
    
    def save_click_data(self):
        """Save the mouse click data to CSV."""
        if not self.mouse_clicks:
            print("No click data to save")
            return
            
        try:
            # Create DataFrame from mouse clicks
            df = pd.DataFrame(self.mouse_clicks)
            
            # Add participant ID
            df['participant_id'] = self.participant_id
            
            # Save to CSV
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(RESULTS_DIR, f"participant_{self.participant_id}_clicks_{timestamp}.csv")
            
            df.to_csv(filename, index=False)
            print(f"Saved click data to {filename}")
            
            # Show success message
            self.status_var.set(f"Click data saved to {os.path.basename(filename)}")
            
        except Exception as e:
            print(f"Error saving click data: {e}")
            self.status_var.set(f"Error saving click data: {str(e)}")

def main():
    app = MinimalExperimentRunner()
    app.root.mainloop()

if __name__ == "__main__":
    main()