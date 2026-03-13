#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PPS Sequential Experiment Runner

This application implements the experimental procedure with a sequential workflow:
1. First select participant 
2. Then start the LSL data stream
3. Finally begin the main experiment

Features:
- Sequential button activation
- Integrated LSL stream support
- Audio instructions playback
- Timeline visualization of experiment progress
- Automatic mouse repositioning before stimuli
"""

import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, font
import numpy as np
import pandas as pd
import threading
import datetime
import re
import glob
import traceback
import json
from pathlib import Path

# Try to import optional dependencies
try:
    import sounddevice as sd
    import soundfile as sf
    AUDIO_AVAILABLE = True
except ImportError:
    print("WARNING: Audio libraries (sounddevice/soundfile) not available")
    AUDIO_AVAILABLE = False

try:
    import pylsl
    LSL_AVAILABLE = True
except ImportError:
    print("WARNING: pylsl not installed. LSL streaming will be disabled.")
    LSL_AVAILABLE = False

try:
    import pyautogui
    MOUSE_CONTROL_AVAILABLE = True
except ImportError:
    print("WARNING: pyautogui not available. Mouse repositioning will be disabled.")
    MOUSE_CONTROL_AVAILABLE = False

# Configuration
BASE_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace"
EXPERIMENT_AUDIO_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentAudio")
EXPERIMENT_LOG_DIR = os.path.join(BASE_DIR, "Level1_AudioGeneration", "ExperimentLog")
RESULTS_DIR = os.path.join(BASE_DIR, "Level2_RunExperiment", "Results")
GENERAL_INSTRUCTIONS_WAV = os.path.join(BASE_DIR, "Level2_RunExperiment", "GeneralInstructions.wav")

# Response window in seconds
RESPONSE_WINDOW = 1.5

# Create required directories
os.makedirs(RESULTS_DIR, exist_ok=True)

class PPSExperimentRunner:
    def __init__(self, root):
        self.root = root
        self.root.title("PPS Experiment Runner")
        
        # Set protocol for window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Initialize variables
        self.participant_id = None
        self.available_participants = []
        self.experiment_running = False
        self.stream_running = False
        self.audio_instructions_playing = False
        self.start_time = None
        self.mouse_clicks = []
        self.tactile_times = []
        self.timeline_markers = []
        self.click_count = 0
        self.audio_duration = 0
        
        # LSL streams
        self.lsl_stream = None
        
        # Flags to control flow
        self.stop_audio = False
        self.participant_selected = False
        self.stream_started = False
        
        # Scan for available participants
        self.scan_available_participants()
        
        # Create the main GUI
        self.create_gui()
        
        # Update button states for sequential flow
        self.update_button_states()

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
        """Create the main GUI with sections matching the blueprint."""
        # Configure grid layout for the main window
        self.root.columnconfigure(0, weight=1)
        
        # Create main container frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.columnconfigure(0, weight=1)
        
        # Create all sections as specified in the blueprint
        current_row = 0
        
        # 1. Participant Selection Section - Blue
        participant_frame = self.create_section(main_frame, "Select Participant", "blue", current_row)
        self.create_participant_selection(participant_frame)
        current_row += 1
        
        # 2. Data Stream Section - Black
        stream_frame = self.create_section(main_frame, "Start Data Stream", "black", current_row)
        self.create_stream_section(stream_frame)
        current_row += 1
        
        # 3. Audio Instructions Section - Orange
        instructions_frame = self.create_section(main_frame, "Play Audio Instructions", "#E67E22", current_row)
        self.create_instructions_section(instructions_frame)
        current_row += 1
        
        # 4. Main Experiment Section - Green
        experiment_frame = self.create_section(main_frame, "Start Main Experiment", "green", current_row)
        self.create_experiment_section(experiment_frame)
        current_row += 1
        
        # 5. Timeline Display Section - Purple
        timeline_frame = self.create_section(main_frame, "Experiment Timeline Tracker Display", "#8E44AD", current_row)
        self.create_timeline_section(timeline_frame)
        current_row += 1
        
        # 6. Mouse Area Section - Gray
        mouse_frame = self.create_section(main_frame, "Mouse Screen Area", "gray", current_row)
        self.create_mouse_area_section(mouse_frame)
        current_row += 1
        
        # Status bar at the bottom
        status_frame = ttk.Frame(main_frame, padding="5")
        status_frame.grid(row=current_row, column=0, sticky="ew", pady=5)
        status_frame.columnconfigure(0, weight=1)
        
        self.status_var = tk.StringVar(value="Ready to start. Please select a participant.")
        status_label = ttk.Label(status_frame, textvariable=self.status_var, font=("Arial", 10, "italic"))
        status_label.grid(row=0, column=0, sticky="w")
        
        # Progress bar
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            main_frame, 
            orient="horizontal",
            mode="determinate",
            variable=self.progress_var
        )
        self.progress_bar.grid(row=current_row+1, column=0, sticky="ew", pady=5)

    def create_section(self, parent, title, color, row):
        """Create a colored section with a title."""
        frame = ttk.Frame(parent, padding="10")
        frame.grid(row=row, column=0, sticky="nsew", pady=5)
        frame.columnconfigure(0, weight=1)
        
        # Add a colored canvas background
        canvas = tk.Canvas(frame, bg=color, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        
        # Add a nested frame for content
        content = ttk.Frame(canvas, padding="15")
        content.columnconfigure(0, weight=1)
        window = canvas.create_window(0, 0, anchor="nw", window=content, width=frame.winfo_reqwidth())
        
        # Bind resize events to update the canvas window
        def configure_canvas(event):
            canvas.configure(width=frame.winfo_width(), height=frame.winfo_height())
            canvas.itemconfig(window, width=frame.winfo_width())
        
        frame.bind("<Configure>", configure_canvas)
        
        # Add title with custom font
        title_font = font.Font(family="Arial", size=14, weight="bold")
        title_label = ttk.Label(content, text=title, font=title_font, foreground="white", background=color)
        title_label.grid(row=0, column=0, sticky="nsew", pady=5)
        
        return content

    def create_participant_selection(self, parent):
        """Create the participant selection section."""
        # Create container for the content
        content_frame = ttk.Frame(parent)
        content_frame.grid(row=1, column=0, sticky="nsew", pady=5)
        content_frame.columnconfigure(0, weight=1)
        
        # Description text
        description = (
            "(creates a file, marking the selection in this folder:\n"
            "\"C:\\Users\\cogpsy-vrlab\\Documents\\GitHub\\BreathingSpace\\Level2_RunExperiment\\Results\n"
            "The file should contain the number of the participant and the current timestamp at which it was selected.\""
        )
        desc_label = ttk.Label(content_frame, text=description, wraplength=600, justify="center", foreground="white")
        desc_label.grid(row=0, column=0, sticky="nsew", pady=5)
        
        # Participant selection controls
        selection_frame = ttk.Frame(content_frame)
        selection_frame.grid(row=1, column=0, sticky="nsew", pady=5)
        selection_frame.columnconfigure(1, weight=1)
        
        # Participant dropdown
        ttk.Label(selection_frame, text="Participant ID:", foreground="white").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        
        self.participant_var = tk.StringVar()
        if self.available_participants:
            self.participant_var.set(str(self.available_participants[0]))
        
        self.participant_dropdown = ttk.Combobox(
            selection_frame, 
            textvariable=self.participant_var,
            values=[str(p) for p in self.available_participants],
            width=10
        )
        self.participant_dropdown.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        # Refresh button
        self.refresh_button = ttk.Button(
            selection_frame, 
            text="Refresh List",
            command=self.refresh_participants
        )
        self.refresh_button.grid(row=0, column=2, padx=5, pady=5, sticky="w")
        
        # Select button
        self.select_button = ttk.Button(
            selection_frame, 
            text="Select Participant",
            command=self.select_participant
        )
        self.select_button.grid(row=0, column=3, padx=5, pady=5, sticky="w")

    def create_stream_section(self, parent):
        """Create the LSL stream section."""
        # Text description
        description = (
            "→ Starts LSL stream module.py\n"
            "Implements participant number as part of stream name, based on\n"
            "the newest file that was saved in the results folder containing the information."
        )
        desc_label = ttk.Label(parent, text=description, wraplength=600, justify="center", foreground="white")
        desc_label.grid(row=0, column=0, sticky="nsew", pady=5)
        
        # Start stream button
        self.start_stream_button = ttk.Button(
            parent, 
            text="Start LSL Data Stream",
            command=self.start_lsl_stream,
            state=tk.DISABLED  # Initially disabled until participant is selected
        )
        self.start_stream_button.grid(row=1, column=0, pady=10)
        
        # Stream status indicator
        self.stream_status_var = tk.StringVar(value="Stream inactive")
        status_label = ttk.Label(parent, textvariable=self.stream_status_var, foreground="white")
        status_label.grid(row=2, column=0, pady=5)

    def create_instructions_section(self, parent):
        """Create the audio instructions section."""
        # Text description
        description = (
            "(Plays this file when clicked \"C:\\Users\\cogpsy-vrlab\\Documents\\GitHub\\BreathingSpace\\Level2_RunExperiment\\GeneralInstructions.wav\",\n"
            "with start, stop and restart buttons"
        )
        desc_label = ttk.Label(parent, text=description, wraplength=600, justify="center", foreground="white")
        desc_label.grid(row=0, column=0, sticky="nsew", pady=5)
        
        # Audio control buttons
        buttons_frame = ttk.Frame(parent)
        buttons_frame.grid(row=1, column=0, pady=10)
        
        self.play_button = ttk.Button(
            buttons_frame, 
            text="▶ Play",
            command=self.play_audio_instructions,
            state=tk.DISABLED  # Initially disabled
        )
        self.play_button.grid(row=0, column=0, padx=5)
        
        self.stop_button = ttk.Button(
            buttons_frame, 
            text="⏹ Stop",
            command=self.stop_audio_instructions,
            state=tk.DISABLED
        )
        self.stop_button.grid(row=0, column=1, padx=5)
        
        self.restart_button = ttk.Button(
            buttons_frame, 
            text="⟳ Restart",
            command=self.restart_audio_instructions,
            state=tk.DISABLED
        )
        self.restart_button.grid(row=0, column=2, padx=5)

    def create_experiment_section(self, parent):
        """Create the main experiment section."""
        # Text description
        description = (
            "Based on the participant number, it selects relevant audio files from:\n"
            "\"C:\\Users\\cogpsy-vrlab\\Documents\\GitHub\\BreathingSpace\\Level1_AudioGeneration\\ExperimentAudio\""
        )
        desc_label = ttk.Label(parent, text=description, wraplength=600, justify="center", foreground="white")
        desc_label.grid(row=0, column=0, sticky="nsew", pady=5)
        
        # Start experiment button
        self.start_experiment_button = ttk.Button(
            parent, 
            text="Start Main Experiment",
            command=self.start_experiment,
            state=tk.DISABLED  # Initially disabled
        )
        self.start_experiment_button.grid(row=1, column=0, pady=10)
        
        # Stop experiment button
        self.stop_experiment_button = ttk.Button(
            parent, 
            text="Stop Experiment",
            command=self.stop_experiment,
            state=tk.DISABLED  # Initially disabled
        )
        self.stop_experiment_button.grid(row=2, column=0, pady=5)

    def create_timeline_section(self, parent):
        """Create the experiment timeline tracker section."""
        # Text description
        description = (
            "(loads the audio file of the participant and creates a timeline which tracks the succession of the two audiofiles that are being played simultaneously)"
        )
        desc_label = ttk.Label(parent, text=description, wraplength=600, justify="center", foreground="white")
        desc_label.grid(row=0, column=0, sticky="nsew", pady=5)
        
        # Timeline canvas
        self.timeline_canvas = tk.Canvas(parent, bg="white", height=80)
        self.timeline_canvas.grid(row=1, column=0, sticky="nsew", pady=5, padx=5)
        
        # Initialize timeline (will be populated when experiment starts)
        self.timeline_start_x = 50
        self.timeline_end_x = 750  # Will be adjusted based on window size
        self.timeline_y = 40
        
        # Create base timeline
        self.timeline_canvas.create_line(
            self.timeline_start_x, self.timeline_y, 
            self.timeline_end_x, self.timeline_y, 
            width=2
        )
        
        # Create progress indicator (will be shown during experiment)
        self.progress_indicator = self.timeline_canvas.create_line(
            self.timeline_start_x, self.timeline_y - 15, 
            self.timeline_start_x, self.timeline_y + 15, 
            width=3, fill="green", state="hidden"
        )

    def create_mouse_area_section(self, parent):
        """Create the mouse screen area section."""
        # Text description
        description = "(Before every looming stimulus is played, the mouse is repositioned at the center of this field in the GUI)"
        desc_label = ttk.Label(parent, text=description, wraplength=600, justify="center", foreground="white")
        desc_label.grid(row=0, column=0, sticky="nsew", pady=5)
        
        # Create mouse click area
        self.click_canvas = tk.Canvas(parent, bg="white", height=150)
        self.click_canvas.grid(row=1, column=0, sticky="nsew", pady=5, padx=5)
        
        # Add text to the click area
        self.click_text = self.click_canvas.create_text(
            300, 75,  # Will be adjusted based on canvas size
            text="CLICK HERE WHEN YOU HEAR THE TACTILE STIMULUS",
            font=("Arial", 14, "bold"), fill="blue"
        )
        
        # Add click counter
        self.click_counter_text = self.click_canvas.create_text(
            50, 20,
            text="Clicks: 0",
            font=("Arial", 10), fill="black"
        )
        
        # Bind mouse clicks
        self.click_canvas.bind("<Button-1>", self.on_mouse_click)
        
        # Bind configure event to adjust text position
        self.click_canvas.bind("<Configure>", self.on_click_canvas_configure)

    def on_click_canvas_configure(self, event):
        """Handle resizing of the click canvas."""
        width = event.width
        height = event.height
        
        # Center the main text
        self.click_canvas.coords(self.click_text, width/2, height/2)
        
        # Keep counter in top-left
        self.click_canvas.coords(self.click_counter_text, 50, 20)

    def update_button_states(self):
        """Update button states based on the current progress."""
        # Stream button is enabled only after participant selection
        self.start_stream_button['state'] = tk.NORMAL if self.participant_selected else tk.DISABLED
        
        # Audio instruction buttons are enabled after stream is started
        instruction_state = tk.NORMAL if self.stream_started else tk.DISABLED
        self.play_button['state'] = instruction_state
        
        # Stop/restart buttons are enabled when audio is playing
        audio_control_state = tk.NORMAL if self.audio_instructions_playing else tk.DISABLED
        self.stop_button['state'] = audio_control_state
        self.restart_button['state'] = audio_control_state
        
        # Experiment button is enabled after stream is started
        self.start_experiment_button['state'] = tk.NORMAL if self.stream_started else tk.DISABLED
        
        # Stop experiment button is enabled when experiment is running
        self.stop_experiment_button['state'] = tk.NORMAL if self.experiment_running else tk.DISABLED

    def refresh_participants(self):
        """Refresh the list of available participants."""
        previous_selection = self.participant_var.get()
        
        # Scan for available participants
        self.scan_available_participants()
        
        # Update dropdown values
        self.participant_dropdown['values'] = [str(p) for p in self.available_participants]
        
        # Try to keep previous selection if it still exists
        if previous_selection in [str(p) for p in self.available_participants]:
            self.participant_var.set(previous_selection)
        elif self.available_participants:
            self.participant_var.set(str(self.available_participants[0]))
        else:
            self.participant_var.set("")
            
        self.status_var.set("Participant list refreshed.")

    def select_participant(self):
        """Select a participant and create the marker file."""
        try:
            participant_id = int(self.participant_var.get())
            self.participant_id = participant_id
            
            # Create timestamp
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Create participant directory in results folder
            participant_dir = os.path.join(RESULTS_DIR, f"participant_{participant_id}")
            os.makedirs(participant_dir, exist_ok=True)
            
            # Create marker file
            marker_file = os.path.join(participant_dir, f"selected_{timestamp}.txt")
            with open(marker_file, 'w') as f:
                f.write(f"Participant {participant_id} selected at {timestamp}")
            
            # Update status
            self.status_var.set(f"Participant {participant_id} selected successfully.")
            
            # Mark participant as selected
            self.participant_selected = True
            
            # Update button states
            self.update_button_states()
            
        except (ValueError, TypeError) as e:
            messagebox.showerror("Error", "Please select a valid participant ID")
            self.status_var.set("Error: Invalid participant selection.")

    def start_lsl_stream(self):
        """Start the LSL stream module."""
        if not LSL_AVAILABLE:
            messagebox.showwarning("LSL Unavailable", "LSL library not available. Install pylsl package.")
            self.status_var.set("LSL functionality not available. Stream will be simulated.")
            # Still mark as started for workflow purposes
            self.stream_started = True
            self.update_button_states()
            return
        
        try:
            # Create LSL stream with participant ID in name
            stream_name = f"PPS_Experiment_P{self.participant_id}"
            stream_id = f"pps_exp_{self.participant_id}"
            
            # Create LSL outlet
            stream_info = pylsl.StreamInfo(
                name=stream_name,
                type='Markers',
                channel_count=1,
                nominal_srate=0,  # Irregular sampling rate
                channel_format='string',
                source_id=stream_id
            )
            
            # Add experiment metadata
            desc = stream_info.desc()
            desc.append_child_value("participant_id", str(self.participant_id))
            
            # Create the outlet
            self.lsl_stream = pylsl.StreamOutlet(stream_info)
            
            # Update status
            self.stream_status_var.set(f"Stream active: {stream_name}")
            self.status_var.set(f"LSL stream started successfully for participant {self.participant_id}.")
            
            # Mark stream as started
            self.stream_started = True
            
            # Send initial marker
            self.send_lsl_marker("stream_started")
            
            # Update button states
            self.update_button_states()
            
        except Exception as e:
            messagebox.showerror("Stream Error", f"Failed to start LSL stream: {str(e)}")
            self.status_var.set(f"Error starting LSL stream: {str(e)}")
            traceback.print_exc()

    def send_lsl_marker(self, marker_text):
        """Send a marker to the LSL stream."""
        if LSL_AVAILABLE and self.lsl_stream:
            try:
                # Include timestamp in the marker
                timestamp = datetime.datetime.now().isoformat()
                full_marker = f"P{self.participant_id}_{marker_text}_{timestamp}"
                
                self.lsl_stream.push_sample([full_marker])
                print(f"LSL marker sent: {marker_text}")
                return True
            except Exception as e:
                print(f"Error sending LSL marker: {e}")
                return False
        return False

    def play_audio_instructions(self):
        """Play the general audio instructions."""
        if not AUDIO_AVAILABLE:
            messagebox.showwarning("Audio Unavailable", "Audio libraries not available.")
            self.status_var.set("Audio functionality not available.")
            return
        
        if self.audio_instructions_playing:
            return
        
        # Check if instruction file exists
        if not os.path.exists(GENERAL_INSTRUCTIONS_WAV):
            messagebox.showerror("File Error", f"Instructions audio file not found at:\n{GENERAL_INSTRUCTIONS_WAV}")
            self.status_var.set("Error: Instructions audio file not found.")
            return
        
        # Start playing in a separate thread
        threading.Thread(target=self._play_instructions_thread, daemon=True).start()
        
        # Update status
        self.status_var.set("Playing audio instructions...")
        
        # Update button states
        self.audio_instructions_playing = True
        self.update_button_states()
        
        # Send LSL marker if stream is active
        self.send_lsl_marker("instructions_start")

    def _play_instructions_thread(self):
        """Thread function to play instructions audio."""
        try:
            # Load audio file
            data, sr = sf.read(GENERAL_INSTRUCTIONS_WAV)
            
            # Convert to mono if stereo
            if len(data.shape) > 1 and data.shape[1] > 1:
                data = np.mean(data, axis=1)
            
            # Play audio
            self.stop_audio = False
            sd.play(data, sr)
            sd.wait()
            
            # Only update if playback completed normally (not stopped)
            if not self.stop_audio:
                # Run in main thread
                self.root.after(0, self._on_instructions_complete)
        except Exception as e:
            print(f"Error playing instructions audio: {e}")
            self.root.after(0, lambda: self.status_var.set(f"Error playing instructions: {str(e)}"))

    def _on_instructions_complete(self):
        """Called when instructions audio completes."""
        self.audio_instructions_playing = False
        self.update_button_states()
        self.status_var.set("Audio instructions completed.")
        self.send_lsl_marker("instructions_end")

    def stop_audio_instructions(self):
        """Stop the audio instructions playback."""
        if not self.audio_instructions_playing:
            return
        
        self.stop_audio = True
        sd.stop()
        
        self.audio_instructions_playing = False
        self.update_button_states()
        
        self.status_var.set("Audio instructions stopped.")
        self.send_lsl_marker("instructions_stopped")

    def restart_audio_instructions(self):
        """Restart the audio instructions playback."""
        self.stop_audio_instructions()
        self.root.after(100, self.play_audio_instructions)  # Small delay to ensure stop completes

    def start_experiment(self):
        """Start the main experiment."""
        if self.experiment_running:
            return
        
        print(f"\n===== STARTING EXPERIMENT FOR PARTICIPANT {self.participant_id} =====")
        
        # Reset experiment state
        self.stop_audio = False
        self.mouse_clicks = []
        self.click_count = 0
        self.experiment_running = True
        self.clear_timeline()
        
        # Update status
        self.status_var.set(f"Starting experiment for participant {self.participant_id}...")
        
        # Update button states
        self.update_button_states()
        
        # Reset click counter
        self.click_canvas.itemconfig(self.click_counter_text, text=f"Clicks: 0")
        
        # Send LSL marker
        self.send_lsl_marker("experiment_start")
        
        # Run experiment in background thread
        threading.Thread(target=self.run_experiment, daemon=True).start()

    def stop_experiment(self):
        """Stop the main experiment."""
        if not self.experiment_running:
            return
            
        self.stop_audio = True
        sd.stop()
        print("Experiment stopped manually")
        
        # Update status
        self.status_var.set("Experiment stopped.")
        
        # Update button states
        self.experiment_running = False
        self.update_button_states()
        
        # Send LSL marker
        self.send_lsl_marker("experiment_stopped")
        
        # Save click data
        self.save_click_data()

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
        
        # Send LSL marker
        self.send_lsl_marker(f"mouse_click_time_{current_time:.3f}")
        
        print(f"Mouse click at {current_time:.3f} seconds")

    def center_mouse_in_click_area(self):
        """Center the mouse cursor in the click area."""
        if not MOUSE_CONTROL_AVAILABLE:
            return False
            
        try:
            # Get the geometry of the click canvas
            canvas_x = self.click_canvas.winfo_rootx()
            canvas_y = self.click_canvas.winfo_rooty()
            canvas_width = self.click_canvas.winfo_width()
            canvas_height = self.click_canvas.winfo_height()
            
            # Calculate center coordinates
            center_x = canvas_x + (canvas_width // 2)
            center_y = canvas_y + (canvas_height // 2)
            
            # Move mouse to center position
            pyautogui.moveTo(center_x, center_y)
            print(f"Mouse centered at screen position: {center_x}, {center_y}")
            
            # Send LSL marker
            self.send_lsl_marker(f"mouse_centered_x{center_x}_y{center_y}")
            
            return True
        except Exception as e:
            print(f"Error centering mouse: {e}")
            return False

    def process_upcoming_tactile(self, current_time):
        """
        Check if a tactile stimulus is about to occur and prepare.
        Centers the mouse in the click area before each stimulus.
        
        Args:
            current_time: Current time in the experiment (seconds)
        """
        # Time before stimulus to start preparing (seconds)
        PREPARE_BEFORE = 0.5
        
        if not self.experiment_running or not self.tactile_times:
            return False
            
        # Find upcoming tactile stimuli
        for t_time in self.tactile_times:
            time_diff = t_time - current_time
            
            # If stimulus will occur within preparation window
            if 0 < time_diff < PREPARE_BEFORE:
                print(f"Preparing for tactile stimulus at {t_time:.3f}s (current: {current_time:.3f}s)")
                
                # Center mouse
                self.root.after(0, self.center_mouse_in_click_area)
                
                # Send LSL marker
                self.send_lsl_marker(f"upcoming_tactile_{t_time:.3f}")
                
                # Remove this time so we don't prepare for it again
                self.tactile_times.remove(t_time)
                return True
                
        return False

    def add_timeline_marker(self, time_sec, color):
        """Add a marker to the timeline at the specified time."""
        if self.audio_duration <= 0:
            return
            
        # Get timeline dimensions
        timeline_width = self.timeline_end_x - self.timeline_start_x
        
        # Calculate position based on time and duration
        position = self.timeline_start_x + (time_sec / self.audio_duration) * timeline_width
        
        # Create marker
        marker = self.timeline_canvas.create_oval(
            position-4, self.timeline_y-4, 
            position+4, self.timeline_y+4, 
            fill=color, outline="black", width=1
        )
        
        self.timeline_markers.append(marker)
        
        # Update progress indicator
        self.timeline_canvas.coords(
            self.progress_indicator, 
            position, self.timeline_y-15, 
            position, self.timeline_y+15
        )
        self.timeline_canvas.itemconfig(self.progress_indicator, state="normal")

    def clear_timeline(self):
        """Clear all markers from the timeline."""
        for marker in self.timeline_markers:
            self.timeline_canvas.delete(marker)
        self.timeline_markers = []
        
        # Hide progress indicator
        self.timeline_canvas.itemconfig(self.progress_indicator, state="hidden")

    def update_progress(self, elapsed_time):
        """Update progress bar and timeline indicator."""
        if self.audio_duration <= 0:
            return
            
        # Update progress bar
        progress_percent = min(100, (elapsed_time / self.audio_duration) * 100)
        self.progress_var.set(progress_percent)
        
        # Update timeline position
        timeline_width = self.timeline_end_x - self.timeline_start_x
        position = self.timeline_start_x + (elapsed_time / self.audio_duration) * timeline_width
        
        self.timeline_canvas.coords(
            self.progress_indicator, 
            position, self.timeline_y-15, 
            position, self.timeline_y+15
        )
        self.timeline_canvas.itemconfig(self.progress_indicator, state="normal")

    def update_timeline_with_duration(self):
        """Update the timeline with time markers based on audio duration."""
        if self.audio_duration <= 0:
            return
        
        # Clear existing time markers but keep the base line
        time_markers = self.timeline_canvas.find_withtag("time_marker")
        for marker in time_markers:
            self.timeline_canvas.delete(marker)
        
        # Create base timeline
        self.timeline_canvas.create_line(
            self.timeline_start_x, self.timeline_y, 
            self.timeline_end_x, self.timeline_y, 
            width=2, tags=["timeline_base"]
        )
        
        # Determine interval based on duration
        if self.audio_duration < 60:
            interval = 10  # 10 second intervals for short audio
        elif self.audio_duration < 300:
            interval = 30  # 30 second intervals for medium audio
        else:
            interval = 60  # 1 minute intervals for longer audio
        
        # Add time markers
        timeline_width = self.timeline_end_x - self.timeline_start_x
        
        for t in range(0, int(self.audio_duration) + interval, interval):
            if t > self.audio_duration:
                break
                
            # Calculate position
            position = self.timeline_start_x + (t / self.audio_duration) * timeline_width
            
            # Create tick
            self.timeline_canvas.create_line(
                position, self.timeline_y - 5,
                position, self.timeline_y + 5,
                width=1,
                tags=["time_marker"]
            )
            
            # Create label
            if t >= 60:
                minutes = t // 60
                seconds = t % 60
                time_label = f"{minutes}:{seconds:02d}"
            else:
                time_label = f"{t}s"
                
            self.timeline_canvas.create_text(
                position, self.timeline_y + 15,
                text=time_label,
                font=("Arial", 8),
                tags=["time_marker"]
            )
            
        # Add markers for all tactile stimuli
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
                self.update_button_states()
                return
            
            # Load tactile stimulus times
            self.tactile_times = self.load_tactile_times(self.participant_id)
            if not self.tactile_times:
                self.status_var.set(f"Warning: No tactile stimulus times found")
                print(f"WARNING: No tactile stimulus times found")
            
            # Make a copy of tactile times list for timeline display
            tactile_times_display = self.tactile_times.copy()
            
            # Load audio files to get duration
            looming_info = sf.info(looming_file)
            tactile_info = sf.info(tactile_file)
            
            # Set audio duration for timeline
            self.audio_duration = looming_info.duration
            print(f"Audio duration: {self.audio_duration:.2f} seconds")
            
            # Update timeline based on duration
            self.root.after(0, self.update_timeline_with_duration)
            
            # Bring window to front and center mouse before starting
            self.root.lift()
            self.root.focus_force()
            self.center_mouse_in_click_area()
            
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
            
            # Send LSL marker
            self.send_lsl_marker("audio_playback_start")
            
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
                
                # Check for upcoming tactile stimuli
                self.process_upcoming_tactile(elapsed)
                
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
                self.status_var.set("Experiment completed successfully")
                
                # Send LSL marker
                self.send_lsl_marker("experiment_completed")
                
                # Save click data
                self.save_click_data()
            
        except Exception as e:
            print(f"ERROR during experiment: {e}")
            traceback.print_exc()
            self.status_var.set(f"Error: {str(e)}")
            self.send_lsl_marker(f"experiment_error_{str(e)}")
        
        finally:
            self.experiment_running = False
            self.update_button_states()

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
            
            # Create participant directory if not exists
            participant_dir = os.path.join(RESULTS_DIR, f"participant_{self.participant_id}")
            os.makedirs(participant_dir, exist_ok=True)
            
            # Save to CSV
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(participant_dir, f"clicks_{timestamp}.csv")
            
            df.to_csv(filename, index=False)
            print(f"Saved click data to {filename}")
            
            # Send LSL marker
            self.send_lsl_marker(f"data_saved_{os.path.basename(filename)}")
            
            # Show success message
            self.status_var.set(f"Click data saved successfully - {len(df)} clicks recorded")
            
        except Exception as e:
            print(f"Error saving click data: {e}")
            self.status_var.set(f"Error saving click data: {str(e)}")
            traceback.print_exc()

    def on_close(self):
        """Handle window close event."""
        if self.experiment_running:
            if messagebox.askyesno("Quit", "Experiment is running. Are you sure you want to quit?"):
                # Stop audio playback when closing the window
                self.stop_audio = True
                sd.stop()
                print("Audio playback stopped")
                
                # Send LSL marker
                self.send_lsl_marker("application_closing")
                
                self.root.destroy()
        else:
            # Send LSL marker if stream exists
            if self.stream_started:
                self.send_lsl_marker("application_closing")
                
            self.root.destroy()

def main():
    root = tk.Tk()
    app = PPSExperimentRunner(root)
    
    # Set window size to 80% of screen
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    window_width = int(screen_width * 0.8)
    window_height = int(screen_height * 0.8)
    
    # Position in center of screen
    x_position = (screen_width - window_width) // 2
    y_position = (screen_height - window_height) // 2
    
    root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
    
    # Start the GUI main loop
    root.mainloop()

if __name__ == "__main__":
    main()