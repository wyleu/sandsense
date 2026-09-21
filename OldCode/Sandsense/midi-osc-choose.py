import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import mido
import threading
import queue

# Function to select MIDI input
def select_midi_input():
    # Get available MIDI inputs
    available_inputs = mido.get_input_names()
    
    if not available_inputs:
        print("No MIDI inputs available!")
        return None
    
    print("Available MIDI inputs:")
    for i, input_name in enumerate(available_inputs):
        print(f"{i}: {input_name}")
    
    while True:
        try:
            choice = int(input("Select MIDI input (enter number): "))
            if 0 <= choice < len(available_inputs):
                return available_inputs[choice]
            else:
                print("Invalid selection. Try again.")
        except ValueError:
            print("Please enter a number.")

# Set up MIDI input queue
midi_queue = queue.Queue()
selected_port = None
midi_thread = None

# MIDI reading thread
def midi_reader(port_name):
    try:
        with mido.open_input(port_name) as inport:
            for msg in inport:
                if msg.type == 'control_change':
                    midi_queue.put((msg.channel, msg.value))
    except Exception as e:
        print(f"MIDI input error: {e}")

# Set up the figure and axes
fig, ax = plt.subplots(figsize=(12, 6))
plt.title('64-Channel MIDI CC Data (8 Hz)')
plt.xlabel('Channel')
plt.ylabel('CC Value (0-127)')

# Initialize data array for 64 channels
channels = np.arange(64)
values = np.zeros(64)
"""
Key additions and changes:
Added select_midi_input() function that:
Lists all available MIDI inputs on the system

Displays them with numbered options

Allows user to select an input by entering a number

Returns the selected port name or None if no ports are available

Improved MIDI handling:
Uses the selected port name to open the MIDI input

Includes error handling for MIDI input

Only starts the MIDI thread if a valid port is selected

Linux-specific considerations:
On Linux, mido uses ALSA backend by default

Will detect MIDI ports available through ALSA

Works with both hardware MIDI ports and virtual MIDI ports

To use this on Linux:
Install dependencies:

bash

pip install matplotlib numpy mido python-rtmidi

Ensure ALSA MIDI is working on your system:

bash

# Check available MIDI ports
aplaymidi -l

Run the script:
It will list all available MIDI inputs

Type the number of the desired input and press Enter

The animation will start with data from the selected MIDI source

Example output when running:

Available MIDI inputs:
0: Midi Through Port-0
1: USB MIDI Interface
2: Virtual MIDI 1
Select MIDI input (enter number): 

The program will:
Show all MIDI inputs detected by ALSA

Wait for your selection

Start reading from the chosen MIDI port

Display the animated graph with 64 channels at 8 Hz

Note: Make sure your MIDI device is connected before running the script, and that you have appropriate permissions to access MIDI devices on your Linux system.


"""
# Create bar plot
bars = ax.bar(channels, values)
ax.set_ylim(0, 127)
ax.set_xlim(-1, 64)

# Animation update function
def update(frame):
    # Process any new MIDI messages
    while not midi_queue.empty():
        channel, value = midi_queue.get()
        if channel < 64:  # Ensure channel is in our range
            values[channel] = value
    
    # Update bar heights
    for bar, value in zip(bars, values):
        bar.set_height(value)
    
    return bars
"""
def update(frame):
    values = [midi_queue.get()[1] if not midi_queue.empty() else values[i] for i in range(64)]
    for bar, val in zip(bars, values):
        bar.set_height(val)
        bar.set_color('red' if val > 100 else 'green' if val > 50 else 'blue')
    return bars,
"""

# Main execution
if __name__ == "__main__":
    # Select MIDI input
    selected_port = select_midi_input()
    
    if selected_port:
        # Start MIDI reading in separate thread
        midi_thread = threading.Thread(target=midi_reader, args=(selected_port,), daemon=True)
        midi_thread.start()
        
        # Create animation
        ani = animation.FuncAnimation(
            fig,
            update,
            frames=None,
            interval=125,  # 8 updates per second
            blit=True
        )
        
        plt.tight_layout()
        plt.show()
    else:
        print("No MIDI port selected. Exiting.")
