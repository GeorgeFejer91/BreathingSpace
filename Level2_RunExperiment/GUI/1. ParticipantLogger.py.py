import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
import datetime
import subprocess

# Output directory for JSON files
output_dir = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\Results\participant_metadata"

# Paths to scripts to run
instructions_script = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\GUI\2. Play Audio Instructions.py"
lsl_recorder_script = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\GUI\3.1 LSLrecorder.py"
main_experiment_script = r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level2_RunExperiment\GUI\3.2 MainExperimentInterface.py"

# Function to validate age input (only allow numbers)
def validate_age(P):
    if P == "":  # Allow empty field
        return True
    return P.isdigit() and 0 <= int(P) <= 120  # Age range check

# Function to handle selection
def on_selection_change(event):
    # Get selected participant number
    selected = dropdown.get()
    if not selected:
        return
        
    participant_num = int(selected)
    
    # Format participant ID with leading zeros (P001, P002, etc.)
    participant_id = f"P{participant_num:03d}"
    
    # Update the confirmation label
    confirm_label.config(text=f"Confirm participant: {participant_id}?")
    
    # Show the confirm button
    confirm_button.grid(row=11, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
    root.update_idletasks()  # Force update of window dimensions
    
    # Adjust window size if needed
    ensure_window_fits()

def ensure_window_fits():
    """Make sure the window is properly sized to fit all widgets"""
    # Get required height for all widgets
    required_height = frame.winfo_reqheight() + 40  # Add some padding
    required_width = frame.winfo_reqwidth() + 40
    
    # Get current window dimensions and screen dimensions
    current_width = root.winfo_width()
    current_height = root.winfo_height()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    
    # Calculate new dimensions ensuring they don't exceed screen size
    new_width = min(max(required_width, current_width), screen_width - 100)
    new_height = min(max(required_height, current_height), screen_height - 100)
    
    # Center the window
    x = (screen_width - new_width) // 2
    y = (screen_height - new_height) // 2
    
    # Set new dimensions and position
    root.geometry(f"{new_width}x{new_height}+{x}+{y}")

# Function to handle confirmation
def on_confirm():
    # Get all participant information
    participant_num = int(dropdown.get())
    first_name = first_name_entry.get().strip()
    last_name = last_name_entry.get().strip()
    age_val = age_entry.get().strip()
    handedness = handedness_dropdown.get()
    gender = gender_dropdown.get()
    
    # Basic validation
    if not first_name or not last_name:
        messagebox.showerror("Error", "Please enter first and last name.")
        return
    
    if not age_val:
        messagebox.showerror("Error", "Please enter age.")
        return
    
    if not handedness:
        messagebox.showerror("Error", "Please select handedness.")
        return
    
    if not gender:
        messagebox.showerror("Error", "Please select gender.")
        return
    
    # Format participant ID with leading zeros (P001, P002, etc.)
    participant_id = f"P{participant_num:03d}"
    
    # Get current timestamp
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    
    # Create metadata dictionary with new fields
    metadata = {
        "participant_id": participant_id,
        "first_name": first_name,
        "last_name": last_name,
        "age": int(age_val),
        "handedness": handedness,
        "gender": gender,
        "timestamp": timestamp
    }
    
    # Create filename with participant ID and timestamp
    filename = f"{participant_id}_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)
    
    # Ensure the output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Write the JSON file
    try:
        with open(filepath, 'w') as f:
            json.dump(metadata, f, indent=2)
        status_label.config(text=f"Created: {filename}")
        
        # Run all three scripts in order
        status_label.config(text=f"Starting applications...")
        
        # 1. Run the Audio Instructions script
        subprocess.Popen(['python', instructions_script])
        
        # 2. Run the LSL Recorder script
        subprocess.Popen(['python', lsl_recorder_script])
        
        # 3. Run the Main Experiment Interface script
        subprocess.Popen(['python', main_experiment_script])
        
        # Close this GUI
        root.destroy()
        
    except Exception as e:
        status_label.config(text=f"Error: {str(e)}")
        messagebox.showerror("Error", f"Failed to create file or start applications: {str(e)}")

# Create the main window
root = tk.Tk()
root.title("Breathing Space Experiment")
root.minsize(450, 500)  # Increased minimum size for new fields

# Calculate center position of screen
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
x = (screen_width - 450) // 2
y = (screen_height - 500) // 2
root.geometry(f"450x500+{x}+{y}")  # Position window in center

# Create a frame with padding
frame = ttk.Frame(root, padding="20")
frame.grid(row=0, column=0, sticky="nsew")  # Use grid for better control

# Configure the root window to expand the frame
root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)

# Configure the frame's grid
frame.columnconfigure(0, weight=1)
frame.columnconfigure(1, weight=1)
for i in range(12):  # Prepare rows for all widgets
    frame.rowconfigure(i, weight=1)

# Add a title
title_label = ttk.Label(frame, text="Breathing Space Experiment", font=("Arial", 14, "bold"))
title_label.grid(row=0, column=0, columnspan=2, pady=(0, 15), sticky="w")

# Add instruction label for participant ID
instruction_label = ttk.Label(frame, text="Select Participant ID:", font=("Arial", 12))
instruction_label.grid(row=1, column=0, columnspan=2, pady=(0, 5), sticky="w")

# Create the dropdown with participant numbers 0-99
dropdown = ttk.Combobox(frame, values=[str(i) for i in range(100)], width=10)
dropdown.grid(row=2, column=0, columnspan=2, pady=(0, 15), sticky="ew")
dropdown.bind("<<ComboboxSelected>>", on_selection_change)

# First Name field
first_name_label = ttk.Label(frame, text="First Name:", font=("Arial", 11))
first_name_label.grid(row=3, column=0, pady=5, sticky="w")
first_name_entry = ttk.Entry(frame)
first_name_entry.grid(row=3, column=1, pady=5, sticky="ew")

# Last Name field
last_name_label = ttk.Label(frame, text="Last Name:", font=("Arial", 11))
last_name_label.grid(row=4, column=0, pady=5, sticky="w")
last_name_entry = ttk.Entry(frame)
last_name_entry.grid(row=4, column=1, pady=5, sticky="ew")

# Age field with validation
age_label = ttk.Label(frame, text="Age:", font=("Arial", 11))
age_label.grid(row=5, column=0, pady=5, sticky="w")
vcmd = (root.register(validate_age), '%P')
age_entry = ttk.Entry(frame, validate="key", validatecommand=vcmd)
age_entry.grid(row=5, column=1, pady=5, sticky="ew")

# Handedness dropdown
handedness_label = ttk.Label(frame, text="Handedness:", font=("Arial", 11))
handedness_label.grid(row=6, column=0, pady=5, sticky="w")
handedness_dropdown = ttk.Combobox(frame, values=["Right", "Left", "Ambidextrous"], state="readonly")
handedness_dropdown.grid(row=6, column=1, pady=5, sticky="ew")

# Gender dropdown
gender_label = ttk.Label(frame, text="Gender:", font=("Arial", 11))
gender_label.grid(row=7, column=0, pady=5, sticky="w")
gender_dropdown = ttk.Combobox(frame, values=["Male", "Female", "Other", "Prefer not to say"], state="readonly")
gender_dropdown.grid(row=7, column=1, pady=5, sticky="ew")

# Add confirmation label
confirm_label = ttk.Label(frame, text="", font=("Arial", 11))
confirm_label.grid(row=9, column=0, columnspan=2, pady=10, sticky="w")

# Add confirm button (initially hidden)
confirm_button = ttk.Button(frame, text="Confirm and Start Experiment", command=on_confirm)
# Not visible until participant is selected

# Add status label to show results
status_label = ttk.Label(frame, text="", font=("Arial", 10))
status_label.grid(row=12, column=0, columnspan=2, pady=10, sticky="w")

# Apply a theme for better appearance
style = ttk.Style()
if 'clam' in style.theme_names():  # Check if the theme is available
    style.theme_use('clam')

# Add some styling
style.configure('TButton', font=('Arial', 11))
style.configure('TLabel', font=('Arial', 11))
style.configure('TFrame', background='#f0f0f0')

# Start the main loop
root.mainloop()