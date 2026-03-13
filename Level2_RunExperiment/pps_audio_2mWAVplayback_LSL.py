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
                 stop_after_minutes=2, shared_timer=None):
        """
        Initialize an LSL audio streamer that streams audio from a loopback device.
        
        Parameters:
        -----------
        device_id : int
            The ID of the audio loopback device to stream.
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
        shared_timer : dict
            Shared timer object to synchronize multiple streamers.
        """
        self.device_id = device_id
        self.channel_name = channel_name
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.silence_threshold = silence_threshold
        self.stop_after_minutes = stop_after_minutes
        self.shared_timer = shared_timer or {'start_time': None, 'running': False, 'lock': threading.Lock()}
        
        self.is_running = False
        
        # Get device info
        device_info = sd.query_devices(device_id)
        self.device_name = device_info['name']
        
        # Create LSL StreamInfo and outlet
        self.info = pylsl.StreamInfo(
            name=channel_name,
            type='Audio',
            channel_count=2,  # Stereo for output devices
            nominal_srate=sample_rate,
            channel_format='float32',
            source_id=f'audio_stream_{device_id}'
        )
        
        # Add metadata
        desc = self.info.desc()
        desc.append_child_value("device_name", self.device_name)
        
        self.outlet = pylsl.StreamOutlet(self.info)
        print(f"Created LSL outlet: {channel_name} - Will stream for {self.stop_after_minutes} minutes after audio detection")
    
    def audio_callback(self, outdata, frames, time_info, status):
        """Callback function for the audio stream when monitoring outputs."""
        if status:
            print(f"Status [{self.channel_name}]: {status}")
            
        # Calculate RMS to detect audio
        rms = np.sqrt(np.mean(outdata**2))
        
        # If timer not started yet and we detect audio, start the timer
        with self.shared_timer['lock']:
            # Check if this is the first detection of audio
            if not self.shared_timer['running'] and rms > self.silence_threshold:
                print(f"Audio detected (RMS: {rms:.4f}) on device \"{self.device_name}\", starting ALL LSL streams...")
                self.shared_timer['start_time'] = datetime.now()
                self.shared_timer['running'] = True
            
            # If timer is running, check if we've exceeded the time limit
            if self.shared_timer['running']:
                elapsed_time = datetime.now() - self.shared_timer['start_time']
                if elapsed_time > timedelta(minutes=self.stop_after_minutes):
                    print(f"Time limit of {self.stop_after_minutes} minutes reached. Stopping stream [{self.channel_name}].")
                    self.is_running = False
                    return
                
                # Prepare zero output to prevent actual audio playback
                outdata.fill(0)
                
                # Send audio data to LSL - flatten stereo channels
                self.outlet.push_chunk(outdata.flatten())
                
                # Print status occasionally (only from one of the streamers to avoid spam)
                if int(elapsed_time.total_seconds()) % 15 == 0 and "1/2" in self.device_name:
                    remaining = timedelta(minutes=self.stop_after_minutes) - elapsed_time
                    print(f"Streaming audio from ALL outputs... Time remaining: {int(remaining.total_seconds())} seconds")
    
    def start(self):
        """Start the audio monitoring and LSL streaming."""
        if self.is_running:
            print(f"Streamer [{self.channel_name}] is already running.")
            return
        
        self.is_running = True
        
        print(f"Monitoring output device: ID {self.device_id}, \"{self.device_name}\" [{self.channel_name}]")
        
        # Start the audio stream
        try:
            # Use OutputStream to monitor output audio
            self.audio_stream = sd.OutputStream(
                device=self.device_id,
                channels=2,  # Stereo for output devices
                samplerate=self.sample_rate,
                blocksize=self.chunk_size,
                callback=self.audio_callback
            )
                
            self.audio_stream.start()
            
            # Keep the stream running until stopped or time limit reached
            while self.is_running:
                time.sleep(0.1)
                
        except Exception as e:
            print(f"Error [{self.channel_name}]: {e}")
        finally:
            if hasattr(self, 'audio_stream'):
                self.audio_stream.stop()
                self.audio_stream.close()
            print(f"Audio streaming stopped for [{self.channel_name}].")
    
    def stop(self):
        """Stop the audio monitoring and LSL streaming."""
        self.is_running = False
        print(f"Stopping audio streamer [{self.channel_name}]...")


def list_audio_devices():
    """List all available audio devices with their IDs."""
    devices = sd.query_devices()
    print("\nAvailable Audio Devices:")
    print("-"*100)
    print(f"{'ID':<5} | {'Input Ch':<9} | {'Output Ch':<10} | {'Name':<70}")
    print("-"*100)
    for i, dev in enumerate(devices):
        print(f"{i:<5} | {dev['max_input_channels']:<9} | {dev['max_output_channels']:<10} | {dev['name']}")
    print("-"*100)


def find_komplete_loopback_devices():
    """Find Komplete Audio output devices for direct streaming."""
    devices = sd.query_devices()
    output1_2 = None
    output3_4 = None
    
    for i, dev in enumerate(devices):
        name_lower = dev['name'].lower()
        
        # Look for Komplete Audio output devices 
        if 'komplete' in name_lower and dev['max_output_channels'] > 0:
            if 'output 1/2' in name_lower:
                output1_2 = (i, dev['name'])
            elif 'output 3/4' in name_lower:
                output3_4 = (i, dev['name'])
    
    return output1_2, output3_4


def create_clean_stream_name(prefix, device_name):
    """Create a clean LSL stream name from the device name."""
    # Remove special characters and replace spaces with underscores
    clean_name = re.sub(r'[^\w\s]', '', device_name)
    clean_name = re.sub(r'\s+', '_', clean_name)
    
    # Extract relevant parts (looking for output 1/2 or 3/4 patterns)
    output_match = re.search(r'Output_(\d+)_(\d+)', clean_name)
    if output_match:
        output_part = f"Out{output_match.group(1)}_{output_match.group(2)}"
    else:
        # Just take the first 15 chars if no specific pattern found
        output_part = clean_name[:15]
    
    # Return combined name
    return f"{prefix}_{output_part}"


def run_output_streamer(device_id, device_name, shared_timer, stop_event):
    """Run a streamer for an output device in its own thread."""
    # Create clean stream name from the output device
    stream_name = create_clean_stream_name("TwoMinAudio", device_name)
    
    # Create and start the streamer with the shared timer
    streamer = LSLAudioStreamer(
        device_id=device_id,
        channel_name=stream_name,
        silence_threshold=0.01,
        shared_timer=shared_timer
    )
    
    # Start the streamer and let it run until the stop event is set
    try:
        streamer.start()
    except Exception as e:
        print(f"Error in streamer thread: {e}")
    finally:
        print(f"Streamer thread for {stream_name} exiting")


if __name__ == "__main__":
    try:
        # List available audio devices
        list_audio_devices()
        
        # Find Komplete loopback devices
        output1_2_loopback, output3_4_loopback = find_komplete_loopback_devices()
        
        # Make sure we found the devices
        if not output1_2_loopback:
            print("Could not find Komplete Output 1/2 loopback device! Check device names, connections, and WASAPI settings.")
            exit(1)
            
        if not output3_4_loopback:
            print("Could not find Komplete Output 3/4 loopback device! Check device names, connections, and WASAPI settings.")
            exit(1)
            
        print(f"\nFound Output 1/2 Loopback: ID {output1_2_loopback[0]}, {output1_2_loopback[1]}")
        print(f"Found Output 3/4 Loopback: ID {output3_4_loopback[0]}, {output3_4_loopback[1]}")
        
        # Create a shared timer to synchronize both streamers
        shared_timer = {
            'start_time': None,
            'running': False,
            'lock': threading.Lock()
        }
        
        # Create a shared stop event
        stop_event = threading.Event()
        
        # Create threads for each output device
        threads = []
        
        print("\nStarting audio streamers for both output channels...")
        print("Both streams will start simultaneously when audio is detected on either channel.")
        print("Both streams will run for exactly 2 minutes from first audio detection.")
        
        # Start streamer for Output 1/2 Loopback
        t1 = threading.Thread(
            target=run_output_streamer,
            args=(output1_2_loopback[0], output1_2_loopback[1], shared_timer, stop_event)
        )
        t1.daemon = True
        t1.start()
        threads.append(t1)
        
        # Start streamer for Output 3/4 Loopback
        t2 = threading.Thread(
            target=run_output_streamer,
            args=(output3_4_loopback[0], output3_4_loopback[1], shared_timer, stop_event)
        )
        t2.daemon = True
        t2.start()
        threads.append(t2)
        
        print("\nBoth streamers started and synchronized. Press Ctrl+C to stop all streamers.")
        
        # Wait for all threads to finish or until interrupted
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            print("\nReceived keyboard interrupt. Stopping all streamers...")
            stop_event.set()
            
            # Give threads a moment to clean up
            time.sleep(1)
            
    except Exception as e:
        print(f"\nError running script: {e}")
        import traceback
        traceback.print_exc()
        
    print("\nScript execution complete.")