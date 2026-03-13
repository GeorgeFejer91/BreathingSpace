import tkinter as tk
from tkinter import ttk, messagebox
import os
import datetime
import json
import glob
import re
from pathlib import Path
import wave
import pygame
from threading import Thread
import time

class PPSExperimentGUI:
    def __init__(self):
        # Initialize pygame mixer for audio
        pygame.mixer.init()
        
        # Base configuration
        self.BASE_DIR = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace"
        self.RESULTS_DIR = os.path.join(self.BASE_DIR, "Level2_RunExperiment", "Results")
        self.AUDIO_DIR = os.path.join(self.BASE_DIR, "Level2_RunExperiment")
        self.INSTRUCTIONS_AUDIO = os.path.join(self.AUDIO_DIR, "GeneralInstructions.wav")
        
        # Ensure directories exist
        os.makedirs(self.RESULTS_DIR, exist_ok=True)
        
        # Initialize state variables
        self.current_participant = None
        self.audio_playing = False
        self.selected_timestamp = None
        
        # Create main window
        self.root = tk.Tk()
        self.root.title("PPS Experiment Interface")
        
        # Configure window size
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = int(screen_width * 0.85)
        window_height = int(screen_height * 0.85)
        self.root.geometry(f"{window_width}x{window_height}")
        
        # Configure grid
        self.root.grid_columnconfigure(0, weight=1)
        for i in range(5):
            self.root.grid_rowconfigure(i, weight=1)
            
        self.create_gui_sections()
        
    def create_gui_sections(self):
        # 1. Participant Selection Section
        participant_frame = ttk.LabelFrame(self.root, text="Select Participant")
        participant_frame.grid(row=0, column=0, padx=10, pady=5, sticky="nsew")
        
        # Participant selection controls
        self.participant_var = tk.StringVar()
        self.participant_var.trace('w', self.on_participant_selected)
        
        ttk.Label(participant_frame, text="Participant ID:").pack(pady=5)
        self.participant_dropdown = ttk.Combobox(
            participant_frame,
            textvariable=self.participant_var,
            values=self.scan_available_participants()
        )
        self.participant_dropdown.pack(pady=5)
        
        self.selection_info = ttk.Label(participant_frame, text="No participant selected", wraplength=400)
        self.selection_info.pack(pady=5)
        
        # 2. Audio Instructions Section
        instructions_frame = ttk.LabelFrame(self.root, text="Play Audio Instructions")
        instructions_frame.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        
        control_frame = ttk.Frame(instructions_frame)
        control_frame.pack(pady=10)
        
        self.start_audio_btn = ttk.Button(control_frame, text="Start", command=self.start_instructions)
        self.start_audio_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_audio_btn = ttk.Button(control_frame, text="Stop", command=self.stop_instructions)
        self.stop_audio_btn.pack(side=tk.LEFT, padx=5)
        
        self.restart_audio_btn = ttk.Button(control_frame, text="Restart", command=self.restart_instructions)
        self.restart_audio_btn.pack(side=tk.LEFT, padx=5)
        
        # 3. Main Experiment Section
        experiment_frame = ttk.LabelFrame(self.root, text="Start Main Experiment")
        experiment_frame.grid(row=2, column=0, padx=10, pady=5, sticky="nsew")
        
        self.start_exp_btn = ttk.Button(
            experiment_frame, 
            text="Start Experiment", 
            command=self.start_experiment
        )
        self.start_exp_btn.pack(pady=10)
        
        # 4. Timeline Section
        timeline_frame = ttk.LabelFrame(self.root, text="Experiment Timeline tracker display")
        timeline_frame.grid(row=3, column=0, padx=10, pady=5, sticky="nsew")
        
        self.timeline_canvas = tk.Canvas(timeline_frame, bg='white', height=100)
        self.timeline_canvas.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 5. Mouse Screen Area
        mouse_frame = ttk.LabelFrame(self.root, text="Mouse Screen Area")
        mouse_frame.grid(row=4, column=0, padx=10, pady=5, sticky="nsew")
        
        self.mouse_canvas = tk.Canvas(mouse_frame, bg='lightgray')
        self.mouse_canvas.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Initialize button states
        self.update_button_states()
        
    def scan_available_participants(self):
        """Scan for available participant numbers and return list"""
        participants = []
        for i in range(1, 100):  # Scan for participants 1-99
            participants.append(f"{i:02d}")  # Format as 2-digit number
        return participants
        
    def on_participant_selected(self, *args):
        """Handle participant selection"""
        if self.participant_var.get():
            try:
                participant_num = int(self.participant_var.get())
                self.current_participant = participant_num
                self.selected_timestamp = datetime.datetime.now()
                
                # Create selection file
                selection_data = {
                    "participant_id": participant_num,
                    "timestamp": self.selected_timestamp.isoformat(),
                    "selection_time": self.selected_timestamp.strftime("%Y%m%d_%H%M%S")
                }
                
                # Create participant-specific directory
                participant_dir = os.path.join(self.RESULTS_DIR, f"participant_{participant_num:02d}")
                os.makedirs(participant_dir, exist_ok=True)
                
                # Save selection file
                selection_file = os.path.join(
                    participant_dir, 
                    f"participant_{participant_num:02d}_selection_{self.selected_timestamp.strftime('%Y%m%d_%H%M%S')}.json"
                )
                
                with open(selection_file, 'w') as f:
                    json.dump(selection_data, f, indent=4)
                
                # Update info label
                self.selection_info.config(
                    text=f"Selected Participant {participant_num:02d}\n"
                         f"Selection saved at: {self.selected_timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                         f"File created in: {participant_dir}"
                )
                
                self.update_button_states()
                
            except ValueError:
                messagebox.showerror("Error", "Invalid participant number")
                self.current_participant = None
                self.selected_timestamp = None
                
    def update_button_states(self):
        """Update button states based on current selection"""
        state = "normal" if self.current_participant else "disabled"
        self.start_audio_btn.config(state=state)
        self.stop_audio_btn.config(state=state)
        self.restart_audio_btn.config(state=state)
        self.start_exp_btn.config(state=state)
        
    def start_instructions(self):
        """Start playing instructions audio"""
        if os.path.exists(self.INSTRUCTIONS_AUDIO):
            try:
                pygame.mixer.music.load(self.INSTRUCTIONS_AUDIO)
                pygame.mixer.music.play()
                self.audio_playing = True
            except Exception as e:
                messagebox.showerror("Error", f"Failed to play audio: {str(e)}")
        else:
            messagebox.showerror("Error", "Instructions audio file not found")
            
    def stop_instructions(self):
        """Stop playing instructions audio"""
        if self.audio_playing:
            pygame.mixer.music.stop()
            self.audio_playing = False
            
    def restart_instructions(self):
        """Restart instructions audio"""
        self.stop_instructions()
        self.start_instructions()
        
    def start_experiment(self):
        """Start the main experiment"""
        if not self.current_participant:
            messagebox.showerror("Error", "Please select a participant first")
            return
            
        # Here you would implement the actual experiment start logic
        messagebox.showinfo("Start Experiment", 
                          f"Starting experiment for Participant {self.current_participant:02d}")
        
    def run(self):
        """Start the GUI application"""
        self.root.mainloop()

def main():
    app = PPSExperimentGUI()
    app.run()

if __name__ == "__main__":
    main()