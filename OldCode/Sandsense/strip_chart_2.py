import mido
import matplotlib.pyplot as plt
from collections import defaultdict
import sys

def parse_midi_file(midi_file_path):
    try:
        # Load MIDI file
        mid = mido.MidiFile(midi_file_path)
    except Exception as e:
        print(f"Error loading MIDI file: {e}")
        sys.exit(1)

    # Store CC messages: {channel: [(time, value)]}
    cc_data = defaultdict(list)
    current_time = 0
    tempo = 500000  # Default tempo (120 BPM)

    # Set ticks per beat
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

def plot_cc_data(cc_data, ticks_per_beat, tempo, midi_file_path):
    if not cc_data:
        print("No Control Change messages found in the MIDI file.")
        return

    # Create a plot
    plt.figure(figsize=(10, 6))

    # Plot each CC channel
    for cc_number, data in cc_data.items():
        times, values = zip(*data)  # Unpack time and value lists
        plt.plot(times, values, label=f'CC {cc_number}', marker='o', linestyle='-')

    # Customize plot
    plt.title(f'MIDI CC Values - {midi_file_path.split("/")[-1]}')
    plt.xlabel('Time (seconds)')
    plt.ylabel('CC Value (0-127)')
    plt.grid(True)
    plt.legend()
    plt.tight_layout()

    # Display plot
    plt.show()

def main():
    if len(sys.argv) != 2:
        print("Usage: python script.py <midi_file_path>")
        sys.exit(1)

    midi_file_path = sys.argv[1]
    cc_data, ticks_per_beat, tempo = parse_midi_file(midi_file_path)
    plot_cc_data(cc_data, ticks_per_beat, tempo, midi_file_path)

if __name__ == "__main__":
    main()
