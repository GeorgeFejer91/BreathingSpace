import sounddevice as sd
import soundfile as sf
import numpy as np
import tkinter as tk
from tkinter import ttk
import threading
import time
import os

class CompactPlayer:
    def __init__(self, file_path):
        self.file_path = file_path
        self.playing = False
        self.paused = False
        self.audio_data = None
        self.sample_rate = 0
        self.position = 0
        self.stream = None
        self.duration = 0
        self.progress_callback = None
        
        # Try to load the file
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                print(f"File not found: {file_path}")
                return
                
            self.audio_data, self.sample_rate = sf.read(self.file_path, dtype='float32')
            print(f"Loaded file: {self.file_path}")
            
            # Convert stereo to mono if needed
            if len(self.audio_data.shape) == 1:
                # Already mono, make it 2D array with one channel
                self.audio_data = self.audio_data.reshape(-1, 1)
            
            # Ensure it's stereo for Output 1/2
            if self.audio_data.shape[1] == 1:
                # Duplicate mono to stereo
                self.audio_data = np.column_stack((self.audio_data, self.audio_data))
                
            # Calculate duration in seconds
            self.duration = len(self.audio_data) / self.sample_rate
                
            print(f"Audio format: {self.audio_data.shape} at {self.sample_rate} Hz")
            print(f"Audio duration: {self.duration:.2f} seconds")
            
        except Exception as e:
            print(f"Could not load file: {e}")
    
    def play(self):
        if self.playing and self.paused:
            # Resume
            self.paused = False
            return True
            
        if self.playing:
            return False  # Already playing
            
        if self.audio_data is None:
            return False  # No audio loaded
        
        # Create playback thread
        self.playing = True
        self.paused = False
        threading.Thread(target=self._play_thread, daemon=True).start()
        return True
        
    def _play_thread(self):
        try:
            # Find the Output 1/2 device
            device_id = None
            devices = sd.query_devices()
            
            # Look for Output 1/2
            for i, dev in enumerate(devices):
                if dev['max_output_channels'] > 0 and 'output 1/2' in dev['name'].lower():
                    device_id = i
                    print(f"Found Output 1/2 device: {i} - {dev['name']}")
                    break
            
            # If not found, use default
            if device_id is None:
                device_id = sd.default.device[1]
                print(f"Output 1/2 not found, using default: {device_id}")
                
            # Play through the selected device
            self.position = 0
            start_time = time.time()
            
            # Simple blocking playback
            print(f"Starting playback on device {device_id}")
            sd.play(self.audio_data, self.sample_rate, device=device_id, blocking=False)
            
            # Wait until finished
            while sd.get_stream().active and self.playing and not self.paused:
                # Update position based on elapsed time
                current_time = time.time()
                elapsed = current_time - start_time
                self.position = min(elapsed, self.duration)
                
                # Call progress callback if set
                if self.progress_callback and not self.paused:
                    self.progress_callback(self.position, self.duration)
                
                time.sleep(0.1)
                
            if self.paused:
                sd.stop()
            else:
                self.playing = False
                # Final update to ensure progress shows 100%
                if self.progress_callback:
                    self.progress_callback(self.duration, self.duration)
                print("Playback completed")
                
        except Exception as e:
            print(f"Playback error: {e}")
            self.playing = False
            self.paused = False
    
    def pause(self):
        if self.playing and not self.paused:
            self.paused = True
            return True
        return False
    
    def stop(self):
        if self.playing:
            self.playing = False
            self.paused = False
            sd.stop()
            return True
        return False
        
    def restart(self):
        self.stop()
        time.sleep(0.1)
        return self.play()
        
    def cleanup(self):
        self.stop()
        sd.stop()


class CompactPlayerApp:
    def __init__(self, root):
        self.root = root
        root.title("Instructions")
        
        # Get screen dimensions
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        
        # Calculate window size (25% of screen)
        window_width = int(screen_width * 0.25)
        window_height = int(screen_height * 0.25)
        
        # Position at top-left corner
        x_position = 0
        y_position = 0
        
        # Set window position and size
        root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        
        # Disable resizing
        root.resizable(False, False)
        
        # Make window stay on top
        root.attributes("-topmost", True)
        
        # Create a player with the specified audio file
        self.player = CompactPlayer(r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\GUI\IntroductionInstructions.mp3")
        
        # Set up progress callback
        self.player.progress_callback = self.update_progress
        
        # Close handler
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Create widgets with minimal space
        main_frame = ttk.Frame(root, padding=5)
        main_frame.pack(expand=True, fill=tk.BOTH)
        
        # Title
        ttk.Label(main_frame, text="Introduction Instructions", font=("Arial", 10, "bold")).pack(pady=3)
        
        # Progress bar
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=3)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            progress_frame, 
            orient="horizontal", 
            length=window_width-20, 
            mode="determinate",
            variable=self.progress_var
        )
        self.progress_bar.pack(fill=tk.X)
        
        # Time display
        self.time_var = tk.StringVar(value="0:00 / 0:00")
        ttk.Label(progress_frame, textvariable=self.time_var, font=("Arial", 8)).pack(anchor=tk.E)
        
        # Status display
        self.status_var = tk.StringVar(value="Ready to play")
        ttk.Label(main_frame, textvariable=self.status_var, font=("Arial", 9)).pack(pady=2)
        
        # Buttons in a compact layout
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=3)
        
        # Play button (larger)
        self.play_btn = ttk.Button(button_frame, text="Play", command=self.play, width=10)
        self.play_btn.grid(row=0, column=0, padx=3, pady=3)
        
        # Other buttons (smaller)
        self.pause_btn = ttk.Button(button_frame, text="Pause", command=self.pause, width=8)
        self.pause_btn.grid(row=0, column=1, padx=3, pady=3)
        
        self.restart_btn = ttk.Button(button_frame, text="Restart", command=self.restart, width=8)
        self.restart_btn.grid(row=0, column=2, padx=3, pady=3)
        
        # Auto-play on startup (optional, comment out if not needed)
        # self.root.after(500, self.play)
    
    def update_progress(self, position, duration):
        """Update progress bar and time display"""
        if duration <= 0:
            return
            
        # Calculate progress percentage
        progress_percent = (position / duration) * 100
        
        # Update progress bar - use root.after to ensure thread safety
        def _update():
            self.progress_var.set(progress_percent)
            
            # Format time display
            pos_min = int(position) // 60
            pos_sec = int(position) % 60
            dur_min = int(duration) // 60
            dur_sec = int(duration) % 60
            
            self.time_var.set(f"{pos_min}:{pos_sec:02d} / {dur_min}:{dur_sec:02d}")
            
        self.root.after(0, _update)
    
    def play(self):
        if self.player.play():
            if self.player.paused:
                self.status_var.set("Playing")
                self.pause_btn.config(text="Pause")
            else:
                self.status_var.set("Playing")
        else:
            self.status_var.set("Already playing")
    
    def pause(self):
        if self.player.playing and not self.player.paused:
            if self.player.pause():
                self.status_var.set("Paused")
                self.pause_btn.config(text="Resume")
        else:
            self.play()  # Resume
    
    def restart(self):
        # Reset progress bar
        self.progress_var.set(0)
        self.time_var.set("0:00 / 0:00")
        
        if self.player.restart():
            self.status_var.set("Playing from start")
            self.pause_btn.config(text="Pause")
    
    def on_close(self):
        self.player.cleanup()
        self.root.destroy()


# Run the application
if __name__ == "__main__":
    root = tk.Tk()
    app = CompactPlayerApp(root)
    root.mainloop()