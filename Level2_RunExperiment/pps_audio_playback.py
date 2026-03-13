import numpy as np
import sounddevice as sd
import re
import pylsl
import time
import threading
from datetime import datetime, timedelta

class LSLAudioStreamer:
    def __init__(self, device_id=None, channel_name="AudioStream", 
                 sample_rate=44100, chunk_size=1024, silence_threshold=0.01, 
                 stop_after_minutes=2):
        """
        Initialize an LSL audio streamer that monitors and streams audio from a specified device.
        
        Parameters:
        -----------
        device_id : int or None
            The ID of the audio device to monitor. If None, the default device is used.
        channel_name : str
            The name of the LSL stream channel.
        sample_rate : int
            Audio sample rate in Hz.
        chunk_size : int
            Number of samples per chunk.
        silence_threshold : float
            The RMS threshold below which audio is considered silence.
        stop_after_minutes : int
            Minutes to continue streaming after audio is first detected.
        """
        self.device_id = device_id
        self.channel_name = channel_name
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.silence_threshold = silence_threshold
        self.stop_after_minutes = stop_after_minutes
        
        self.is_running = False
        self.stream_started = False
        self.start_time = None
        
        # Create LSL StreamInfo and outlet
        self.info = pylsl.StreamInfo(
            name=channel_name,
            type='Audio',
            channel_count=1,
            nominal_srate=sample_rate,
            channel_format='float32',
            source_id='audio_stream_001'
        )
        
        self.outlet = pylsl.StreamOutlet(self.info)
        print(f"Created LSL outlet: {channel_name}")
        
        # Add device info to the stream metadata
        device_info = sd.query_devices(device_id) if device_id is not None else sd.query_devices(sd.default.device[0])
        desc = self.info.desc()
        device_node = desc.append_child("device")
        device_node.append_child_value("name", device_info['name'])
        device_node.append_child_value("channels", str(device_info['max_input_channels']))
        device_node.append_child_value("sample_rate", str(self.sample_rate))
    
    def audio_callback(self, indata, frames, time_info, status):
        """Callback function for the audio stream."""
        if status:
            print(f"Status: {status}")
            
        # Calculate RMS of the audio chunk to detect silence
        rms = np.sqrt(np.mean(indata**2))
        
        if not self.stream_started and rms > self.silence_threshold:
            print(f"Audio detected (RMS: {rms:.4f}), starting LSL stream...")
            self.stream_started = True
            self.start_time = datetime.now()
        
        # Only push data if stream has started
        if self.stream_started:
            # Check if we've exceeded the time limit
            elapsed_time = datetime.now() - self.start_time
            if elapsed_time > timedelta(minutes=self.stop_after_minutes):
                print(f"Time limit of {self.stop_after_minutes} minutes reached. Stopping stream.")
                self.is_running = False
                return
            
            # Send audio data to LSL
            self.outlet.push_chunk(indata.flatten())
            
            # Print status occasionally
            if int(elapsed_time.total_seconds()) % 15 == 0:
                remaining = timedelta(minutes=self.stop_after_minutes) - elapsed_time
                print(f"Streaming audio... Time remaining: {int(remaining.total_seconds())} seconds")
    
    def start(self):
        """Start the audio monitoring and LSL streaming."""
        if self.is_running:
            print("Streamer is already running.")
            return
        
        self.is_running = True
        self.stream_started = False
        self.start_time = None
        
        print(f"Monitoring audio on device: {self.device_id if self.device_id is not None else 'default'}")
        print(f"Waiting for audio above threshold: {self.silence_threshold}")
        
        # Start the audio input stream
        try:
            self.audio_stream = sd.InputStream(
                device=self.device_id,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.chunk_size,
                callback=self.audio_callback
            )
            self.audio_stream.start()
            
            # Keep the script running until manually stopped or time limit reached
            while self.is_running:
                time.sleep(0.1)
                
        except Exception as e:
            print(f"Error: {e}")
        finally:
            if hasattr(self, 'audio_stream'):
                self.audio_stream.stop()
                self.audio_stream.close()
            print("Audio streaming stopped.")
    
    def stop(self):
        """Stop the audio monitoring and LSL streaming."""
        self.is_running = False
        print("Stopping audio streamer...")


def list_audio_devices():
    """List all available audio devices with their IDs."""
    devices = sd.query_devices()
    print("\nAvailable Audio Devices:")
    print("-"*50)
    for i, dev in enumerate(devices):
        print(f"ID: {i}, Name: {dev['name']}, Input Channels: {dev['max_input_channels']}")
    print("-"*50)


def find_output_device():
    """Find a Komplete audio interface device containing 'output 1/2' in its name."""
    devices = sd.query_devices()
    
    # First pass: Look for Komplete device with "output 1/2"
    for i, dev in enumerate(devices):
        if 'komplete' in dev['name'].lower() and 'output 1/2' in dev['name'].lower():
            print(f"Found Komplete output 1/2 device: ID {i}, {dev['name']}")
            return i
    
    # Second pass: Look for any Komplete output device
    for i, dev in enumerate(devices):
        if 'komplete' in dev['name'].lower() and 'output' in dev['name'].lower():
            print(f"Found Komplete output device: ID {i}, {dev['name']}")
            return i
    
    # Third pass: Any Komplete device that can be monitored
    for i, dev in enumerate(devices):
        if 'komplete' in dev['name'].lower() and dev['max_input_channels'] > 0:
            print(f"Found Komplete device: ID {i}, {dev['name']}")
            return i
    
    # Fourth pass: Fall back to generic output device search
    for i, dev in enumerate(devices):
        if 'output 1/2' in dev['name'].lower():
            print(f"Found generic output 1/2 device: ID {i}, {dev['name']}")
            return i
    
    # Fifth pass: Look for any output device
    for i, dev in enumerate(devices):
        if re.search(r'output\s*\d+\s*/\s*\d+', dev['name'].lower()) and dev['max_input_channels'] > 0:
            print(f"Found alternative output device: ID {i}, {dev['name']}")
            return i
    
    print("No suitable Komplete or output device found. Using default input device.")
    return None

if __name__ == "__main__":
    try:
        # List available audio devices
        list_audio_devices()
        
        # Automatically find Komplete device with "output 1/2" in the name
        device_id = find_output_device()
        
        # Use default settings
        channel_name = "AudioStream"
        silence_threshold = 0.01
        
        print(f"\nStarting LSL streamer with the following settings:")
        print(f"- Device ID: {device_id}")
        print(f"- Channel name: {channel_name}")
        print(f"- Silence threshold: {silence_threshold}")
        print(f"- Auto-stop after: 2 minutes")
        
        # Create and start the streamer
        streamer = LSLAudioStreamer(
            device_id=device_id,
            channel_name=channel_name,
            silence_threshold=silence_threshold
        )
        
        # Start streaming directly (not in a thread since this is the main script)
        streamer.start()
        
    except KeyboardInterrupt:
        print("\nScript terminated by user.")
        
    except Exception as e:
        print(f"\nError running script: {e}")
        import traceback
        traceback.print_exc()
        
    print("\nScript execution complete.")