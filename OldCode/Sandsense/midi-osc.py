import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import mido
import threading
import queue

# Set up MIDI input queue
midi_queue = queue.Queue()

# MIDI reading thread
def midi_reader():
    # Open first available MIDI input port
    with mido.open_input() as inport:
        for msg in inport:
            if msg.type == 'control_change':
                midi_queue.put((msg.channel, msg.value))

# Start MIDI reading in separate thread
midi_thread = threading.Thread(target=midi_reader, daemon=True)
midi_thread.start()

# Set up the figure and axes
fig, ax = plt.subplots(figsize=(12, 6))
plt.title('64-Channel MIDI CC Data (8 Hz)')
plt.xlabel('Channel')
plt.ylabel('CC Value (0-127)')

# Initialize data array for 64 channels
channels = np.arange(64)
values = np.zeros(64)

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


text = """
Key features of this program:
Displays 64 channels as vertical bars

Values range from 0-127 (standard MIDI CC range)

Updates 8 times per second (125ms interval)

Uses a bar graph for clear visualization

The second version includes actual MIDI input support

Notes:
The first version uses random data as a demonstration. The second version requires a MIDI input device.

For the MIDI version, you'll need a MIDI controller connected to your computer.

The graph updates at 8 Hz (8 times per second) as requested.

The x-axis shows channel numbers 0-63, and the y-axis shows CC values 0-127.

To use the MIDI version:
Connect a MIDI device

Install the required libraries

Run the script

Send CC messages from your MIDI controller to see the bars update

The bars will update in real-time as MIDI CC messages are received, maintaining the 8 Hz refresh rate even if MIDI messages arrive more or less frequently.


Here's a Python program that creates an animated Matplotlib graph to display 64 channels of MIDI Control Change (CC) data with values ranging from 0-127, updating 8 times per second:
python

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from random import randint  # Using random data as placeholder for MIDI input

# Set up the figure and axes
fig, ax = plt.subplots(figsize=(12, 6))
plt.title('64-Channel MIDI CC Data (8 Hz)')
plt.xlabel('Channel')
plt.ylabel('CC Value (0-127)')

# Initialize data array for 64 channels
channels = np.arange(64)
values = np.zeros(64)

# Create bar plot
bars = ax.bar(channels, values)
ax.set_ylim(0, 127)  # MIDI CC range
ax.set_xlim(-1, 64)

# Function to generate dummy MIDI data (replace with actual MIDI input in real application)
def generate_midi_data():
    return [randint(0, 127) for _ in range(64)]

# Animation update function
def update(frame):
    # Get new MIDI data (using random values as example)
    new_values = generate_midi_data()
    
    # Update bar heights
    for bar, new_value in zip(bars, new_values):
        bar.set_height(new_value)
    
    return bars

# Create animation
# interval = 125ms (1000ms / 8 updates = 125ms between frames)
ani = animation.FuncAnimation(
    fig, 
    update, 
    frames=None,
    interval=125,  # 8 updates per second
    blit=True
)

plt.tight_layout()
plt.show()







"""


