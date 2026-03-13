import os
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from glob import glob
from datetime import datetime
from pylsl import resolve_streams
from liesl import Recorder

class LSLRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LSL Recorder")
        
        # Make window stay on top
        self.root.attributes("-topmost", True)
        
        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Set window size to 25% of screen
        window_width = int(screen_width * 0.25)
        window_height = int(screen_height * 0.25)
        
        # Position at bottom left
        x_position = 0
        y_position = screen_height - window_height
        
        # Set window size and position
        self.root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        
        # Configuration paths
        self.METADATA_FOLDER = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\Results\participant_metadata"
        self.OUTPUT_FOLDER = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\Results\LSL_Output"
        
        # Ensure output folder exists
        os.makedirs(self.OUTPUT_FOLDER, exist_ok=True)
        
        # Variables
        self.recorder = None
        self.recording = False
        self.matched_streams = []
        self.participant_id = tk.StringVar(value="None")
        self.status_var = tk.StringVar(value="Initializing...")
        
        # Create UI
        self.create_ui()
        
        # Get participant ID and scan for streams
        self.refresh_participant_id()
        
        # Auto-start recording after a short delay to allow UI to initialize
        self.root.after(1000, self.auto_start_recording)
    
    def create_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Top section with participant info
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=2)
        
        # Participant ID
        ttk.Label(top_frame, text="Participant:").pack(side=tk.LEFT, padx=2)
        ttk.Label(top_frame, textvariable=self.participant_id, font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Refresh", command=self.refresh_and_restart, width=7).pack(side=tk.RIGHT, padx=2)
        
        # Stream display frame
        stream_frame = ttk.LabelFrame(main_frame, text="Matched Streams (Auto-selected)", padding=3)
        stream_frame.pack(fill=tk.BOTH, expand=True, pady=2)
        
        # Stream listbox (read-only, just for display)
        self.stream_listbox = tk.Listbox(stream_frame, height=3, font=("Arial", 8), state="normal", selectmode="browse")
        self.stream_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        stream_scrollbar = ttk.Scrollbar(stream_frame, orient=tk.VERTICAL, command=self.stream_listbox.yview)
        stream_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.stream_listbox.config(yscrollcommand=stream_scrollbar.set)
        
        # Status and stop button
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=2)
        
        ttk.Label(bottom_frame, textvariable=self.status_var, font=("Arial", 8)).pack(side=tk.LEFT, padx=2)
        
        self.stop_button = ttk.Button(bottom_frame, text="STOP", command=self.stop_recording, width=5)
        self.stop_button.pack(side=tk.RIGHT, padx=2)
    
    def scan_streams(self):
        self.status_var.set("Scanning...")
        
        # Clear listbox and streams list
        self.stream_listbox.delete(0, tk.END)
        self.matched_streams = []
        
        # Get participant ID without the "P" prefix
        participant_num = self.participant_id.get().replace("P", "").strip()
        
        try:
            # Get active streams with a timeout
            all_streams = resolve_streams(wait_time=1.0)
            
            if not all_streams:
                self.status_var.set("No streams found")
                return False
            
            # Filter streams for participant ID
            for stream in all_streams:
                stream_name = stream.name().lower()
                if participant_num in stream_name or f"p{participant_num}" in stream_name:
                    self.matched_streams.append(stream)
                    self.stream_listbox.insert(tk.END, f"{stream.name()} ({stream.type()})")
            
            # If no matching streams, use all streams
            if not self.matched_streams:
                self.status_var.set("No matching streams, using all")
                self.matched_streams = all_streams
                for stream in all_streams:
                    self.stream_listbox.insert(tk.END, f"{stream.name()} ({stream.type()})")
            
            # Update status
            if self.matched_streams:
                self.status_var.set(f"{len(self.matched_streams)} streams ready")
                return True
                
        except Exception as e:
            print(f"Error scanning: {str(e)}")
            self.status_var.set("Scan error")
        
        return False
    
    def refresh_participant_id(self):
        try:
            json_files = glob(os.path.join(self.METADATA_FOLDER, "*.json"))
            
            if not json_files:
                self.participant_id.set("None")
                return False
            
            # Get most recently modified file
            latest_file = max(json_files, key=os.path.getmtime)
            
            with open(latest_file, "r") as f:
                metadata = json.load(f)
            
            if "participant_id" not in metadata:
                self.participant_id.set("Error")
                return False
            
            participant_id = metadata["participant_id"]
            self.participant_id.set(participant_id)
            print(f"Found participant: {participant_id}")
            
            # Refresh streams based on new participant
            return self.scan_streams()
            
        except Exception as e:
            print(f"Error refreshing participant ID: {str(e)}")
            self.participant_id.set("Error")
            return False
    
    def refresh_and_restart(self):
        """Refresh participant and restart recording"""
        if self.recording:
            self.stop_recording()
        
        if self.refresh_participant_id():
            self.auto_start_recording()
    
    def auto_start_recording(self):
        """Automatically start recording"""
        if self.recording:
            return
        
        if self.participant_id.get() in ["None", "Error"]:
            self.status_var.set("No valid participant ID")
            return
        
        if not self.matched_streams:
            self.status_var.set("No streams to record")
            return
        
        # Start recording in a separate thread
        threading.Thread(target=self._recording_thread, daemon=True).start()
    
    def _recording_thread(self):
        try:
            self.root.after(0, lambda: self.status_var.set("Starting..."))
            
            # Prepare output file path
            participant_id = self.participant_id.get()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = os.path.join(self.OUTPUT_FOLDER, f"{participant_id}_{timestamp}.xdf")
            
            # Create streamargs list
            streamargs = []
            for stream in self.matched_streams:
                streamargs.append({
                    "name": stream.name(),
                    "type": stream.type(),
                    "source_id": stream.source_id()
                })
                print(f"Recording stream: {stream.name()} ({stream.type()})")
            
            # Create recorder and start recording
            self.recorder = Recorder()
            self.recorder.start_recording(filename=output_filename, streamargs=streamargs)
            
            # Update status
            self.recording = True
            self.root.after(0, lambda: self.status_var.set(f"Recording {len(self.matched_streams)} streams..."))
            print(f"Recording started to {output_filename}")
            
        except Exception as e:
            print(f"Error starting recording: {str(e)}")
            self.root.after(0, lambda: self.status_var.set("Error starting recording"))
            self.root.after(0, lambda: messagebox.showerror("Error", f"Recording error: {str(e)}"))
    
    def stop_recording(self):
        if not self.recording or not self.recorder:
            return
        
        try:
            self.recorder.stop_recording()
            print("Recording stopped")
            self.recording = False
            self.recorder = None
            
            # Update UI
            self.status_var.set("Ready - Recording stopped")
            
        except Exception as e:
            print(f"Error stopping recording: {str(e)}")
            messagebox.showerror("Error", f"Stop error: {str(e)}")
            
            # Reset state even if there was an error
            self.recording = False
            self.recorder = None
            self.status_var.set("Ready (after error)")
    
    def on_closing(self):
        if self.recording:
            if messagebox.askyesno("Quit", "Recording in progress. Stop and quit?"):
                self.stop_recording()
                self.root.destroy()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = LSLRecorderApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()