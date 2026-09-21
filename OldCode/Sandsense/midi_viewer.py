import mido
import matplotlib.pyplot as plt
from collections import defaultdict
import sys
import os
import time

def select_midi_input_port(backend='mido.backends.rtmidi/ALSA'):
    """
    List available ALSA MIDI input ports and prompt user to select one.
    
    Args:
        backend (str): Mido backend to use (default: ALSA for Linux).
    
    Returns:
        str: Name of the selected MIDI input port, or None if no ports are available or user skips.
    """
    try:
        # Get list of available MIDI input ports
        available_ports = mido.get_input_names(backend=backend)
        
        if not available_ports:
            print("No ALSA MIDI input ports found. Ensure MIDI devices are connected.")
            return None
        
        # Display available ports
        print("\nAvailable ALSA MIDI Input Ports:")
        for i, port in enumerate(available_ports):
            print(f"{i + 1}: {port}")
        print("0: Skip (use MIDI file only)")
        
        # Prompt user for selection
        while True:
            try:
                choice = int(input("\nSelect a port (enter number, 0 to skip): "))
                if choice == 0:
                    return None
                if 1 <= choice <= len(available_ports):
                    return available_ports[choice - 1]
                else:
                    print(f"Please select a number between 0 and {len(available_ports)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")
    
    except Exception as e:
        print(f"Error accessing ALSA MIDI ports: {e}")
        return None

def parse_midi_file(midi_file_path):
    """
    Parse a MIDI file and extract CC messages.
    
    Args:
        midi_file_path (str): Path to the MIDI file.
    
    Returns:
        dict: CC data {cc_number: [(time, value)]}
        int: Ticks per beat
        int: Tempo (microseconds per beat)
    """
    try:
        # Load MIDI file
        mid = mido.MidiFile(midi_file_path)
    except Exception as e:
        print(f"Error loading MIDI file: {e}")
        sys.exit(1)

    # Store CC messages: {cc_number: [(time, value)]}
    cc_data = defaultdict(list)
    current_time = 0
    tempo = 500000  # Default tempo (120 BPM)
    ticks_per_beat = mid.ticks_per_beat

    # Process each track
    for track in mid.tracks:
        for msg in track:
            # Update current time (in ticks)
            current_time += msg.time

            if msg.type == 'set_tempo':
                tempo = msg.tempo
            elif msg.type == 'control_change':
                # Convert time from ticks to seconds
                seconds = mido.tick2second(current_time, ticks_per_beat, tempo)
                cc_data[msg.control].append((seconds, msg.value))

    return cc_data, ticks_per_beat, tempo

def monitor_live_input(port_name, duration=10, backend='mido.backends.rtmidi/ALSA'):
    """
    Monitor live ALSA MIDI input for a specified duration and collect CC messages.
    
    Args:
        port_name (str): Name of the ALSA MIDI input port
        duration (int): Duration to monitor in seconds
        backend (str): Mido backend to use (default: ALSA)
    
    Returns:
        dict: Live CC data {cc_number: [(time, value)]}
    """
    live_cc_data = defaultdict(list)
    try:
        with mido.open_input(port_name, backend=backend) as port:
            print(f"Monitoring {port_name} for {duration} seconds...")
            start_time = time.time()
            
            for msg in port:
                if time.time() - start_time > duration:
                    break
                if msg.type == 'control_change':
                    elapsed_time = time.time() - start_time
                    live_cc_data[msg.control].append((elapsed_time, msg.value))
                    
    except Exception as e:
        print(f"Error monitoring live input: {e}")
    
    return live_cc_data

def plot_cc_data(cc_data, ticks_per_beat, tempo, midi_file_path, live_data=None):
    """
    Plot CC values from MIDI file and optional live data.
    
    Args:
        cc_data (dict): CC data from MIDI file {cc_number: [(time, value)]}
        ticks_per_beat (int): Ticks per beat from MIDI file
        tempo (int): Tempo in microseconds per beat
        midi_file_path (str): Path to MIDI file
        live_data (dict, optional): Live CC data {cc_number: [(time, value)]}
    """
    if not cc_data and not live_data:
        print("No Control Change messages found.")
        return

    # Create a plot
    plt.figure(figsize=(10, 6))

    # Plot MIDI file CC data
    if cc_data:
        for cc_number, data in cc_data.items():
            times, values = zip(*data)
            plt.plot(times, values, label=f'CC {cc_number} (File)', marker='o', linestyle='-')

    # Plot live CC data if
