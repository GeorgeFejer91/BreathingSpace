#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Enhanced PPS Experiment Runner with Auto-Focus and Mouse Centering

Features:
- Loading and playing two perfectly synchronized audio files
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
        
        # LSL streams
        self.lsl_mouse_stream = None
        self.lsl_audio_stream = None
        
        # Flag to control audio playback
        self.stop_audio = False
        
        # Scan for available participants
        self.scan_available_participants()
        
        # Initialize LSL if available
        if LSL_AVAILABLE:
            self.initialize_lsl_streams()
        
        # Create GUI
        self.create_gui()
        
        # Make window appear on top at startup
        self.bring_window_to_front()

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
        
        # Button row
        button_frame = ttk.Frame(controls_container)
        button_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=5)
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
        
        # Bind mouse clicks
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
    
    def process_click_reaction_time(self, click_time, click_data):
        """
        Process a mouse click to determine if it was a response to a tactile stimulus.
        Calculate reaction time and update the results CSV if appropriate.
        
        Args:
            click_time: Time in seconds into the audio (adjusted for start offset)
            click_data: Dictionary with click information
        """
        if self.results_df is None or self.tactile_times is None:
            return
            
        # Find the closest preceding tactile stimulus
        closest_tactile_time = None
        closest_trial_idx = None
        min_diff = float('inf')
        
        # Check if this click corresponds to a tactile stimulus
        for idx, row in self.results_df.iterrows():
            # Skip catch trials and already responded trials
            if row['trial_type'] == 'catch' or pd.notna(row.get('reaction_time')):
                continue
                
            # Get tactile time
            if pd.isna(row.get('tactile_time_seconds')):
                continue
                
            tactile_time = float(row['tactile_time_seconds'])
            
            # Only consider tactile times that are after our start offset
            if tactile_time < (self.start_offset_minutes * 60) and not self.recovery_phase:
                continue
                
            # Calculate time difference
            time_diff = click_time - tactile_time
            
            # Valid response: after tactile but within response window
            if 0 <= time_diff <= RESPONSE_WINDOW and time_diff < min_diff:
                min_diff = time_diff
                closest_tactile_time = tactile_time
                closest_trial_idx = idx
        
        # If a valid response was found
        if closest_trial_idx is not None:
            print(f"Valid response to trial {self.results_df.loc[closest_trial_idx, 'trial_number']}")
            print(f"Reaction time: {min_diff:.3f}s")
            
            # Format the click timestamp in MM:SS.S format (same as tactile timestamps)
            formatted_timestamp = self.format_time_mmsss(click_time)
            
            # Calculate reaction time in milliseconds
            reaction_time_ms = int(min_diff * 1000)  # Convert seconds to milliseconds
            
            # Update the results DataFrame
            self.results_df.loc[closest_trial_idx, 'reaction_time'] = min_diff
            self.results_df.loc[closest_trial_idx, 'reaction_time_ms'] = reaction_time_ms
            self.results_df.loc[closest_trial_idx, 'response_time'] = click_data.get('time')  # Experiment time
            self.results_df.loc[closest_trial_idx, 'audio_time'] = click_time  # Actual audio time
            self.results_df.loc[closest_trial_idx, 'response_timestamp'] = formatted_timestamp
            self.results_df.loc[closest_trial_idx, 'responded'] = True
            self.results_df.loc[closest_trial_idx, 'response_x'] = click_data['x']
            self.results_df.loc[closest_trial_idx, 'response_y'] = click_data['y']
            self.results_df.loc[closest_trial_idx, 'recovery_phase'] = self.recovery_phase
            
            # Display the response information
            trial_num = self.results_df.loc[closest_trial_idx, 'trial_number']
            tactile_ts = self.results_df.loc[closest_trial_idx, 'tactile_stimulus_timestamp']
            self.response_var.set(
                f"Trial {trial_num}: Response at {formatted_timestamp}\n"
                f"Tactile at {tactile_ts}\n"
                f"Reaction time: {min_diff:.3f}s ({reaction_time_ms} ms)"
            )
            
            # Save the updated results
            self.save_results_csv()
    
    def create_results_csv(self):
        """Create a results CSV file based on the design file."""
        if self.participant_id is None or self.design_df is None:
            print("Cannot create results CSV: Missing participant ID or design data")
            return False
            
        try:
            # Ensure participant results directory exists
            self.ensure_participant_results_dir()
            
            # Create a copy of the design DataFrame
            self.results_df = self.design_df.copy()
            
            # Add columns for results
            self.results_df['audio_start_time'] = None
            self.results_df['start_offset_minutes'] = self.start_offset_minutes
            self.results_df['reaction_time'] = None
            self.results_df['reaction_time_ms'] = None  # Added millisecond reaction time
            self.results_df['response_time'] = None
            self.results_df['audio_time'] = None  # Actual time in audio
            self.results_df['response_timestamp'] = None  # Added formatted timestamp
            self.results_df['responded'] = False
            self.results_df['response_x'] = None
            self.results_df['response_y'] = None
            self.results_df['recovery_phase'] = False
            
            # Create timestamp for the filename
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Create the results file path in participant directory
            self.results_file = os.path.join(
                self.participant_results_dir, 
                f"participant_{self.participant_id}_results_{timestamp}.csv"
            )
            
            # Save the initial CSV
            self.save_results_csv()
            
            print(f"Created results CSV: {self.results_file}")
            return True
            
        except Exception as e:
            print(f"Error creating results CSV: {e}")
            traceback.print_exc()
            return False
    
    def save_results_csv(self):
        """Save the current state of the results DataFrame to CSV."""
        if self.results_df is None or self.results_file is None:
            return False
            
        try:
            self.results_df.to_csv(self.results_file, index=False)
            print(f"Updated results CSV: {self.results_file}")
            return True
        except Exception as e:
            print(f"Error saving results CSV: {e}")
            return False
    
    def finalize_results_csv(self):
        """Add final information to the results CSV and save it."""
        if self.results_df is None:
            return
            
        try:
            # Add experiment end time
            end_time = datetime.datetime.now().isoformat()
            
            # Create a summary row
            summary_data = {
                'trial_number': 0,
                'trial_type': 'summary',
                'audio_start_time': self.audio_start_time,
                'start_offset_minutes': self.start_offset_minutes,
                'experiment_end_time': end_time
            }
            
            # Calculate hit rate and false alarm rate
            non_catch_trials = self.results_df[self.results_df['trial_type'] != 'catch']
            
            # Only consider trials after the start offset
            if self.start_offset_minutes > 0:
                start_offset_seconds = self.start_offset_minutes * 60
                valid_trials = non_catch_trials[
                    (pd.isna(non_catch_trials['tactile_time_seconds'])) |
                    (non_catch_trials['tactile_time_seconds'] >= start_offset_seconds)
                ]
                valid_catch_trials = self.results_df[
                    (self.results_df['trial_type'] == 'catch') &
                    (
                        (pd.isna(self.results_df['tactile_time_seconds'])) |
                        (self.results_df['tactile_time_seconds'] >= start_offset_seconds)
                    )
                ]
            else:
                valid_trials = non_catch_trials
                valid_catch_trials = self.results_df[self.results_df['trial_type'] == 'catch']
            
            hit_count = valid_trials['responded'].sum()
            hit_rate = hit_count / len(valid_trials) if len(valid_trials) > 0 else 0
            
            false_alarm_count = valid_catch_trials['responded'].sum()
            false_alarm_rate = false_alarm_count / len(valid_catch_trials) if len(valid_catch_trials) > 0 else 0
            
            # Add to summary
            summary_data['hit_rate'] = hit_rate
            summary_data['hit_count'] = hit_count
            summary_data['false_alarm_rate'] = false_alarm_rate
            summary_data['false_alarm_count'] = false_alarm_count
            summary_data['response_window_seconds'] = RESPONSE_WINDOW
            summary_data['total_non_catch_trials'] = len(valid_trials)
            summary_data['total_catch_trials'] = len(valid_catch_trials)
            
            # Calculate missed trials count
            missed_trials = valid_trials[~valid_trials['responded']].shape[0]
            summary_data['missed_trials_count'] = missed_trials
            
            # Add recovery information
            summary_data['recovery_performed'] = self.recovery_phase
            
            # Add reaction time statistics
            reaction_times = valid_trials['reaction_time'].dropna()
            reaction_times_ms = valid_trials['reaction_time_ms'].dropna()
            
            if len(reaction_times) > 0:
                summary_data['mean_reaction_time'] = reaction_times.mean()
                summary_data['median_reaction_time'] = reaction_times.median()
                summary_data['min_reaction_time'] = reaction_times.min()
                summary_data['max_reaction_time'] = reaction_times.max()
                
                summary_data['mean_reaction_time_ms'] = reaction_times_ms.mean()
                summary_data['median_reaction_time_ms'] = reaction_times_ms.median()
                summary_data['min_reaction_time_ms'] = reaction_times_ms.min()
                summary_data['max_reaction_time_ms'] = reaction_times_ms.max()
            
            # Add summary as a new row
            self.results_df = pd.concat([self.results_df, pd.DataFrame([summary_data])], ignore_index=True)
            
            # Save the final CSV
            self.save_results_csv()
            
            # Also save a copy with 'FINAL' in the filename
            final_file = self.results_file.replace('.csv', '_FINAL.csv')
            self.results_df.to_csv(final_file, index=False)
            print(f"Saved final results to: {final_file}")
            
            # Display a summary message
            if len(reaction_times) > 0:
                summary_message = (
                    f"Experiment completed!\n\n"
                    f"Hit rate: {hit_rate*100:.1f}% ({hit_count}/{len(valid_trials)})\n"
                    f"False alarms: {false_alarm_count}\n"
                    f"Missed trials: {missed_trials}\n"
                    f"Mean RT: {reaction_times.mean():.3f}s ({int(reaction_times_ms.mean())} ms)\n\n"
                    f"Results saved to: {os.path.basename(final_file)}"
                )
            else:
                summary_message = (
                    f"Experiment completed!\n\n"
                    f"Hit rate: {hit_rate*100:.1f}% ({hit_count}/{len(valid_trials)})\n"
                    f"False alarms: {false_alarm_count}\n"
                    f"Missed trials: {missed_trials}\n"
                    f"No valid responses recorded\n\n"
                    f"Results saved to: {os.path.basename(final_file)}"
                )
            
            # Add recovery and offset information to summary
            if self.start_offset_minutes > 0:
                summary_message += f"\n\nStarted at {self.start_offset_minutes:.1f} minutes into audio"
                
            if self.recovery_phase:
                summary_message += f"\nRecovery phase was performed"
            
            self.status_var.set(summary_message)
            print(summary_message)
            
        except Exception as e:
            print(f"Error finalizing results CSV: {e}")
            traceback.print_exc()
    
    def check_for_recovery_files(self):
        """
        Check if recovery audio files exist for the current participant.
        Uses thorough path checking and explicit debugging.
        
        Returns:
            Tuple of (looming_file, tactile_file) if they exist, else (None, None)
        """
        if self.participant_id is None:
            print("Cannot check for recovery files: No participant ID set")
            return None, None
        
        print("\n======= RECOVERY FILE CHECK =======")
        print(f"Checking recovery files for participant {self.participant_id}")
        # Print current participant_results_dir
        print(f"Self.participant_results_dir = {self.participant_results_dir}")
        
        # Format participant ID with leading zeros for consistent matching
        participant_str = f"{self.participant_id:02d}"
        
        # DIRECT CHECK - Check the exact paths you provided
        direct_looming = f"C:\\Users\\cogpsy-vrlab\\Documents\\GitHub\\BreathingSpace\\Level2_RunExperiment\\Results\\participant_{participant_str}\\participant_{participant_str}_missed_looming.wav"
        direct_tactile = f"C:\\Users\\cogpsy-vrlab\\Documents\\GitHub\\BreathingSpace\\Level2_RunExperiment\\Results\\participant_{participant_str}\\participant_{participant_str}_missed_tactile.wav"
        
        print(f"DIRECT CHECK - Checking these exact paths:")
        print(f"Looming: {direct_looming}")
        print(f"Tactile: {direct_tactile}")
        print(f"Files exist? Looming: {os.path.exists(direct_looming)}, Tactile: {os.path.exists(direct_tactile)}")
        
        if os.path.exists(direct_looming) and os.path.exists(direct_tactile):
            print("SUCCESS: Found recovery files with direct path check!")
            return direct_looming, direct_tactile
        
        # If direct check fails, try using participant_results_dir
        base_path = self.participant_results_dir
        print(f"\nUsing base path: {base_path}")
        print(f"Base path exists? {os.path.exists(base_path)}")
        
        # If participant_results_dir doesn't exist, create it and check again
        if not os.path.exists(base_path):
            print(f"Creating participant results directory: {base_path}")
            os.makedirs(base_path, exist_ok=True)
        
        # Standard check with normal extensions
        looming_file = os.path.join(base_path, f"participant_{participant_str}_missed_looming.wav")
        tactile_file = os.path.join(base_path, f"participant_{participant_str}_missed_tactile.wav")
        
        print(f"\nSTANDARD CHECK - Checking these paths:")
        print(f"Looming: {looming_file}")
        print(f"Tactile: {tactile_file}")
        print(f"Files exist? Looming: {os.path.exists(looming_file)}, Tactile: {os.path.exists(tactile_file)}")
        
        if os.path.exists(looming_file) and os.path.exists(tactile_file):
            print("SUCCESS: Found recovery files with standard check!")
            return looming_file, tactile_file
        
        # List all files in the directory to see what's actually there
        print(f"\nListing all files in {base_path}:")
        try:
            files = os.listdir(base_path)
            for file in files:
                print(f"- {file}")
        except Exception as e:
            print(f"Error listing directory: {e}")
        
        # Also check parent directory
        parent_dir = os.path.dirname(base_path)
        print(f"\nListing all files in parent directory {parent_dir}:")
        try:
            files = os.listdir(parent_dir)
            for file in files:
                full_path = os.path.join(parent_dir, file)
                if os.path.isdir(full_path):
                    print(f"- {file}/ (directory)")
                else:
                    print(f"- {file}")
        except Exception as e:
            print(f"Error listing parent directory: {e}")
        
        # Try pattern matching as a fallback
        looming_pattern = os.path.join(base_path, f"*missed_looming*")
        tactile_pattern = os.path.join(base_path, f"*missed_tactile*")
        
        looming_matches = glob.glob(looming_pattern)
        tactile_matches = glob.glob(tactile_pattern)
        
        print(f"\nPATTERN CHECK - Using patterns:")
        print(f"Looming pattern: {looming_pattern}")
        print(f"Tactile pattern: {tactile_pattern}")
        print(f"Matches found? Looming: {looming_matches}, Tactile: {tactile_matches}")
        
        if looming_matches and tactile_matches:
            print("SUCCESS: Found recovery files with pattern matching!")
            return looming_matches[0], tactile_matches[0]
        
        print("\nNo recovery files found using any method.")
        print("======= END RECOVERY FILE CHECK =======\n")
        return None, None
    
    def load_design_data(self, participant_id):
        """Load design data and tactile stimulus times for a participant."""
        try:
            # Load design file
            design_file = os.path.join(EXPERIMENT_LOG_DIR, f"participant_{participant_id}_design.csv")
            print(f"Loading design data from: {design_file}")
            
            self.design_df = pd.read_csv(design_file)
            print(f"Loaded design with {len(self.design_df)} rows")
            
            # Extract tactile stimulus times
            self.tactile_times = []
            
            # Mark non-catch trials without response as "missed" by default
            # This will be updated as responses come in
            for idx, row in self.design_df.iterrows():
                # Initialize response tracking columns for all trials
                if row['trial_type'] != 'catch':
                    self.design_df.loc[idx, 'responded'] = False
                    
                # Skip catch trials (no tactile stimulus)
                if row['trial_type'] == 'catch':
                    continue
                    
                # Check if tactile timestamp is available
                if 'tactile_stimulus_timestamp' in row and pd.notna(row['tactile_stimulus_timestamp']):
                    ts_str = row['tactile_stimulus_timestamp']
                    time_sec = self.parse_timestamp(ts_str)
                    
                    if time_sec is not None:
                        # Store the time in seconds in the DataFrame for easier access
                        self.design_df.loc[idx, 'tactile_time_seconds'] = time_sec
                        self.tactile_times.append(time_sec)
            
            # Sort tactile times chronologically
            self.tactile_times.sort()
            print(f"Loaded {len(self.tactile_times)} tactile stimulus times")
            return True
            
        except Exception as e:
            print(f"Error loading design data: {e}")
            traceback.print_exc()
            return False
    
    def parse_timestamp(self, timestamp_str):
        """
        Parse timestamp in format MM:SS.S to seconds.
        Handles timestamps like '01:37.6'
        """
        if pd.isna(timestamp_str):
            return None
            
        # Match MM:SS.S format
        match = re.match(r'(\d+):(\d+\.\d+)', timestamp_str)
        if match:
            minutes, seconds = match.groups()
            return float(minutes) * 60 + float(seconds)
        return None
    
    def start_experiment(self):
        """
        Start the experiment.
        - Parse start offset
        - Load design data
        - Create results CSV
        - Play audio files
        - Track responses
        """
        # Get participant ID
        try:
            self.participant_id = int(self.participant_var.get())
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Please select a valid participant ID")
            return
        
        # Get start offset
        try:
            self.start_offset_minutes = float(self.start_time_var.get() or "0.0")
        except (ValueError, TypeError):
            messagebox.showerror("Error", "Invalid start time. Please enter a valid number of minutes.")
            return
        
        print(f"\n===== STARTING EXPERIMENT FOR PARTICIPANT {self.participant_id} =====")
        print(f"Start offset: {self.start_offset_minutes:.2f} minutes")
        
        # Create participant results directory
        self.ensure_participant_results_dir()
        
        # Update LSL streams with participant ID if needed
        if LSL_AVAILABLE:
            self.initialize_lsl_streams(update_participant_id=self.participant_id)
        
        # Load design data
        if not self.load_design_data(self.participant_id):
            messagebox.showerror("Error", "Failed to load design data")
            return
        
        # Create results CSV
        if not self.create_results_csv():
            messagebox.showerror("Error", "Failed to create results CSV")
            return
        
        # Reset state
        self.stop_audio = False
        self.mouse_clicks = []
        self.click_count = 0
        self.experiment_running = True
        self.recovery_phase = False
        self.recovery_checked = False
        self.next_tactile_processed = set()  # Reset processed tactile set
        self.clear_timeline()
        
        # Reset response display
        self.response_var.set("No responses yet")
        self.recovery_var.set("Recovery not started")
        
        # Update status
        offset_msg = f" (starting at {self.start_offset_minutes:.1f} minutes)" if self.start_offset_minutes > 0 else ""
        self.status_var.set(f"Starting experiment for participant {self.participant_id}{offset_msg}...")
        
        # Update UI
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.start_time_entry.config(state=tk.DISABLED)
        self.click_canvas.itemconfig(self.click_counter_text, text=f"Clicks: 0")
        
        # Bring window to front at experiment start
        self.bring_window_to_front()
        
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
        self.start_time_entry.config(state=tk.NORMAL)
        
        # Finalize results
        self.finalize_results_csv()

    def add_timeline_marker(self, time_sec, color):
        """Add a marker to the timeline at the specified time."""
        if self.audio_duration <= 0:
            return
            
        # Make sure timeline canvases are properly sized
        self.update_timeline_canvases()
            
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
                fill=color, outline="black", width=1,
                tags=["timeline_marker"]
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
                fill=color, outline="black", width=1,
                tags=["timeline_marker"]
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
        # Clear all markers using tags
        self.timeline_canvas1.delete("timeline_marker")
        self.timeline_canvas2.delete("timeline_marker")
        
        # Also clear our marker lists
        self.timeline_markers1 = []
        self.timeline_markers2 = []
        
        # Hide progress lines
        self.timeline_canvas1.itemconfig(self.progress_line1, state="hidden")
        self.timeline_canvas2.itemconfig(self.progress_line2, state="hidden")

    def initialize_progress_display(self, start_offset_seconds):
        """
        Initialize progress display as if the experiment had been running 
        for the specified amount of time. Mark "past" events.
        """
        if self.audio_duration <= 0:
            return
        
        # Make sure timeline canvases are properly sized
        self.update_timeline_canvases()
        
        # Set progress bar to show fast-forwarded position
        progress_percent = min(100, (start_offset_seconds / self.audio_duration) * 100)
        self.progress_var.set(progress_percent)
        
        # Calculate the halfway point of the audio
        halfway_time = self.audio_duration / 2
        
        # Update the progress lines to show current position
        timeline_start_x = 50
        timeline_width = self.timeline_end_x - timeline_start_x
        
        if start_offset_seconds < halfway_time:
            # First half timeline
            x_pos = timeline_start_x + (start_offset_seconds / halfway_time) * timeline_width
            
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
            self.timeline_canvas1.itemconfig(self.progress_line1, state="normal")
            
            # Update second timeline progress line
            adjusted_time = start_offset_seconds - halfway_time
            x_pos = timeline_start_x + (adjusted_time / halfway_time) * timeline_width
            
            self.timeline_canvas2.coords(
                self.progress_line2, 
                x_pos, self.timeline_y2 - 15, 
                x_pos, self.timeline_y2 + 15
            )
            self.timeline_canvas2.itemconfig(self.progress_line2, state="normal")
        
        # Mark "past" tactile events as gray
        # Show ALL tactile events on the timeline, but color code them
        for t_time in self.tactile_times:
            # Use gray for events that have already happened
            color = "gray" if t_time < start_offset_seconds else "blue"
            # Add marker to timeline - adjust marker time for display purposes
            if t_time <= halfway_time:
                # First half timeline
                x_pos = timeline_start_x + (t_time / halfway_time) * timeline_width
                marker = self.timeline_canvas1.create_oval(
                    x_pos-4, self.timeline_y1-4, x_pos+4, self.timeline_y1+4, 
                    fill=color, outline="black", width=1,
                    tags=["timeline_marker"]
                )
                self.timeline_markers1.append(marker)
            else:
                # Second half timeline
                adjusted_time = t_time - halfway_time
                x_pos = timeline_start_x + (adjusted_time / halfway_time) * timeline_width
                marker = self.timeline_canvas2.create_oval(
                    x_pos-4, self.timeline_y2-4, x_pos+4, self.timeline_y2+4, 
                    fill=color, outline="black", width=1,
                    tags=["timeline_marker"]
                )
                self.timeline_markers2.append(marker)

    def update_progress(self, elapsed_time):
        """Update progress bar and timeline progress lines."""
        if self.audio_duration <= 0:
            return
        
        # Make sure timeline canvases are properly sized
        self.update_timeline_canvases()
            
        # Add start offset to elapsed time for progress calculation
        adjusted_time = elapsed_time + (self.start_offset_minutes * 60)
        
        # Update progress bar based on full audio duration 
        progress_percent = min(100, (adjusted_time / self.audio_duration) * 100)
        self.progress_var.set(progress_percent)
        
        # Calculate the halfway point of the audio
        halfway_time = self.audio_duration / 2
        
        # Update the appropriate timeline progress line
        timeline_start_x = 50
        timeline_width = self.timeline_end_x - timeline_start_x
        
        if adjusted_time <= halfway_time:
            # First half timeline
            x_pos = timeline_start_x + (adjusted_time / halfway_time) * timeline_width
            
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
            adjusted_time_half2 = adjusted_time - halfway_time
            x_pos = timeline_start_x + (adjusted_time_half2 / halfway_time) * timeline_width
            
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
            for item in canvas.find_withtag("timeline_marker"):
                canvas.delete(item)
            
            # Only delete text items that aren't the click counter
            for item in canvas.find_withtag("timeline_text"):
                if item != self.click_counter_text:
                    canvas.delete(item)
        
        # Make sure we have the latest timeline dimensions
        self.update_timeline_canvases()
        
        # Get current timeline dimensions
        timeline_start_x = 50
        timeline_width = self.timeline_end_x - timeline_start_x
        
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
            self.timeline_canvas1.create_line(
                x_pos, self.timeline_y1 - 5, 
                x_pos, self.timeline_y1 + 5, 
                width=1,
                tags=["timeline_marker"]
            )
            
            # Format time label
            if sec >= 60:
                # Show as minutes:seconds for longer intervals
                mins = sec // 60
                secs = sec % 60
                label = f"{mins}:{secs:02d}"
            else:
                # Show as seconds for shorter intervals
                label = f"{sec}s"
                
            self.timeline_canvas1.create_text(
                x_pos, self.timeline_y1 + 12, 
                text=label, 
                font=("Arial", 8),
                tags=["timeline_text"]
            )
        
        # Add time markers to second timeline
        for sec in range(0, int(halfway_time) + interval, interval):
            if sec > halfway_time:
                break
                
            x_pos = timeline_start_x + (sec / halfway_time) * timeline_width
            
            # Calculate actual time (offset by halfway point)
            actual_sec = sec + int(halfway_time)
            
            # Create tick mark
            self.timeline_canvas2.create_line(
                x_pos, self.timeline_y2 - 5, 
                x_pos, self.timeline_y2 + 5, 
                width=1,
                tags=["timeline_marker"]
            )
            
            # Format time label
            if actual_sec >= 60:
                # Show as minutes:seconds for longer intervals
                mins = actual_sec // 60
                secs = actual_sec % 60
                label = f"{mins}:{secs:02d}"
            else:
                # Show as seconds for shorter intervals
                label = f"{actual_sec}s"
                
            self.timeline_canvas2.create_text(
                x_pos, self.timeline_y2 + 12, 
                text=label, 
                font=("Arial", 8),
                tags=["timeline_text"]
            )
        
        # Tactile markers will be added by initialize_progress_display
        # based on the start offset
    
    def play_audio_files_synchronized(self, looming_file, tactile_file, start_offset_seconds=0, is_recovery=False):
        """
        Play audio files using sounddevice, starting from the specified offset with perfect synchronization.
        
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
            
            # SYNCHRONIZED PLAYBACK APPROACH
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
                
                # Start audio streams
                try:
                    # Try to use specific devices if possible
                    looming_device = 0
                    tactile_device = 1
                    
                    try:
                        # First try with specific devices
                        looming_stream = sd.OutputStream(
                            samplerate=looming_sr,
                            channels=1,
                            callback=looming_callback,
                            device=looming_device,
                            finished_callback=lambda: audio_done.set()
                        )
                        tactile_stream = sd.OutputStream(
                            samplerate=tactile_sr,
                            channels=1,
                            callback=tactile_callback,
                            device=tactile_device
                        )
                    except:
                        print("Falling back to default output devices")
                        
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
                    
                finally:
                    # Clean up streams
                    if looming_stream:
                        looming_stream.stop()
                        looming_stream.close()
                    if tactile_stream:
                        tactile_stream.stop()
                        tactile_stream.close()
                
            except Exception as e:
                print(f"Error in synchronized audio playback: {e}")
                traceback.print_exc()
                return False
                
        except Exception as e:
            print(f"Error loading audio files: {e}")
            traceback.print_exc()
            return False
    
    def run_experiment(self):
        """
        Run the experiment (play audio files and track responses).
        - Record audio start timestamp
        - Send LSL markers
        - Update results CSV with experiment start time
        - Check for recovery files near the end of main audio
        - Play recovery audio if available
        """
        try:
            # Get audio file paths for main experiment
            looming_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{self.participant_id}_design_looming.wav")
            tactile_file = os.path.join(EXPERIMENT_AUDIO_DIR, f"participant_{self.participant_id}_design_tactile.wav")
            
            # Verify files exist
            if not os.path.exists(looming_file) or not os.path.exists(tactile_file):
                self.status_var.set(f"Error: Audio files not found")
                print(f"ERROR: Missing audio files")
                self.experiment_running = False
                self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
                self.root.after(0, lambda: self.start_time_entry.config(state=tk.NORMAL))
                return
            
            # Load audio files to get duration
            looming_info = sf.info(looming_file)
            tactile_info = sf.info(tactile_file)
            
            # Verify sample rates match
            if looming_info.samplerate != tactile_info.samplerate:
                self.status_var.set(f"Error: Sample rate mismatch between audio files")
                print(f"ERROR: Sample rate mismatch - Looming: {looming_info.samplerate}, Tactile: {tactile_info.samplerate}")
                self.experiment_running = False
                self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
                self.root.after(0, lambda: self.start_time_entry.config(state=tk.NORMAL))
                return
            
            # Check if start offset is valid
            if self.start_offset_minutes * 60 >= looming_info.duration:
                self.status_var.set(f"Error: Start offset ({self.start_offset_minutes:.1f} min) exceeds audio duration")
                print(f"ERROR: Start offset ({self.start_offset_minutes:.1f} min) exceeds audio duration ({looming_info.duration/60:.1f} min)")
                self.experiment_running = False
                self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
                self.root.after(0, lambda: self.start_time_entry.config(state=tk.NORMAL))
                return
            
            # Set audio duration for timeline
            self.audio_duration = looming_info.duration
            print(f"Audio duration: {self.audio_duration:.2f} seconds")
            
            # Calculate start offset in seconds
            start_offset_seconds = self.start_offset_minutes * 60
            
            # Update timeline based on duration
            self.root.after(0, self.update_timeline_with_duration)
            
            # Initialize progress display to show current position and past events
            self.root.after(0, lambda: self.initialize_progress_display(start_offset_seconds))
            
            # Bring window to front and center mouse in preparation for experiment
            self.root.after(0, self.bring_window_to_front)
            self.root.after(0, self.center_mouse_in_click_area)
            
            # Record audio start timestamp
            self.audio_start_time = datetime.datetime.now().isoformat()
            
            # Update CSV with audio start time
            if self.results_df is not None:
                self.results_df['audio_start_time'] = self.audio_start_time
                self.save_results_csv()
            
            # Set start time just before playback
            self.start_time = time.perf_counter()
            print(f"Starting audio playback at {self.audio_start_time} (offset: {self.start_offset_minutes:.2f} min)")
            
            # Progress update loop
            offset_msg = f" (starting at {self.start_offset_minutes:.1f} min)" if self.start_offset_minutes > 0 else ""
            self.status_var.set(f"Experiment running{offset_msg} - click when you hear the tactile stimulus")
            
            # Calculate remaining audio duration
            remaining_duration = looming_info.duration - start_offset_seconds
            
            # Create thread for progress updates
            def update_progress_thread():
                update_interval = 0.1  # seconds
                check_recovery_threshold = remaining_duration - 10.0  # Check for recovery 10 seconds before end
                
                # Loop until audio is finished or stopped
                while self.experiment_running and not self.stop_audio:
                    elapsed = time.perf_counter() - self.start_time
                    
                    # Break if elapsed time exceeds remaining duration plus buffer
                    if elapsed > remaining_duration + 2.0:
                        break
                    
                    # Update progress UI
                    self.root.after(0, lambda t=elapsed: self.update_progress(t))
                    
                    # Update status with time - show actual position in audio
                    # Calculate actual audio position (including offset)
                    actual_position = elapsed + start_offset_seconds
                    minutes = int(actual_position // 60)
                    seconds = int(actual_position % 60)
                    total_minutes = int(self.audio_duration // 60)
                    total_seconds = int(self.audio_duration % 60)
                    
                    self.root.after(0, lambda m=minutes, s=seconds, tm=total_minutes, ts=total_seconds: 
                                  self.status_var.set(f"Experiment running - {m:02d}:{s:02d} / {tm:02d}:{ts:02d}"))
                    
                    # Check for recovery files near the end
                    if elapsed > check_recovery_threshold and not self.recovery_checked:
                        print("Checking for recovery files (approaching end of main audio)...")
                        self.recovery_checked = True
                        
                        self.root.after(0, lambda: self.recovery_var.set("Checking for missed trials..."))
                        
                        # Update results to mark missed trials
                        self.save_results_csv()
                    
                    time.sleep(update_interval)
            
            # Start progress update thread
            progress_thread = threading.Thread(target=update_progress_thread, daemon=True)
            progress_thread.start()
            
            # Play main experiment audio with synchronized playback
            main_completed = self.play_audio_files_synchronized(
                looming_file, tactile_file, start_offset_seconds
            )
            
            if not main_completed or self.stop_audio:
                # Experiment was stopped or had an error
                self.finalize_results_csv()
                self.experiment_running = False
                self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
                self.root.after(0, lambda: self.start_time_entry.config(state=tk.NORMAL))
                return
            
            # Check for recovery files after main audio completes
            print("\n====== STARTING RECOVERY FILE CHECK ======")
            print(f"Current participant_results_dir: {self.participant_results_dir}")
            print(f"Current participant_id: {self.participant_id}")
            recovery_looming, recovery_tactile = self.check_for_recovery_files()
            print(f"Recovery check result - Looming: {recovery_looming}, Tactile: {recovery_tactile}")
            print("====== END RECOVERY FILE CHECK ======\n")
            
            if recovery_looming and recovery_tactile:
                # We have recovery files to play
                print(f"Playing recovery audio files...")
                self.root.after(0, lambda: self.recovery_var.set("Recovery files found. Starting missed trials..."))
                
                # Bring window to front for recovery phase
                self.root.after(0, self.bring_window_to_front)
                self.root.after(0, self.center_mouse_in_click_area)
                
                # Play recovery audio with synchronized playback (no offset for recovery)
                recovery_completed = self.play_audio_files_synchronized(
                    recovery_looming, recovery_tactile, 0, is_recovery=True
                )
                
                if recovery_completed:
                    print("Recovery audio completed successfully")
                    self.root.after(0, lambda: self.recovery_var.set("Recovery phase completed successfully"))
                else:
                    print("Recovery audio was interrupted")
                    self.root.after(0, lambda: self.recovery_var.set("Recovery phase was interrupted"))
            else:
                # No recovery files or they're not ready
                print("No recovery files found after main experiment")
                self.root.after(0, lambda: self.recovery_var.set("No missed trials to recover"))
            
            # Experiment complete
            print(f"Audio playback completed at {datetime.datetime.now().isoformat()}")
            self.status_var.set("Experiment completed")
            
            # Send LSL marker for experiment completion
            if LSL_AVAILABLE:
                self.send_lsl_audio_marker("experiment_complete")
            
            # Finalize results
            self.finalize_results_csv()
            
        except Exception as e:
            print(f"ERROR during experiment: {e}")
            traceback.print_exc()
            self.status_var.set(f"Error: {str(e)}")
        
        finally:
            self.experiment_running = False
            
            # Update UI
            self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))
            self.root.after(0, lambda: self.start_time_entry.config(state=tk.NORMAL))


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