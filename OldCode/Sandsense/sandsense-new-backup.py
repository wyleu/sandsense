import threading
import time
import datetime
import logging
import random
import os
import sys

import smbus2
import RPi.GPIO as GPIO
import i2cEncoderLibV2
import rrdtool
import ADS1x15
import VL53L0X
import mido   # pip install python-rtmidi

from queue import Queue, Empty
from pathlib import Path

date_str = datetime.datetime.now().strftime("Logfile_%Y:%m:%d:%H:%M:%S")

distance_logging = False

beats_per_minute = 48
beats_per_hour = beats_per_minute * 60 


class Message():
    def __init__(self, name, function):
        self.name = name
        self.function = function
        self.last_time = 0
        
    def __str__(self):
        return (f'MESSAGE: {self.name}')
        
    def consume(self):
        t= time.time()
        diff = self.last_time - t
        self.last_time = t
        logging.info(f' {t} {self.name} {diff} {response}') 
    
    
        
message = {
        'HOUR' : Message('HOUR', None),
        'MINUTE' : Message('MINUTE', None),
        'HALF' : Message('HALF', None),
        'QUARTER' : Message('QUARTER', None),
        'THREE QUARTER' : Message('THREE QUARTER', None),
        'TICK ON' : Message('TICK ON', None),
        'TICK OFF' : Message('TICK OFF', None),
}

def map_range(x, in_min, in_max, out_min, out_max):
  return (x - in_min) * (out_max - out_min) // (in_max - in_min) + out_min

# User Questions:
# - Please write a python programme with three threads, one which generates a one second tick and two helper threads that acknowledge 
#   the tick, perform an action and then respond over two separate bi directional queues
# - The name of Empty is incorrect, you should import Empty from queue and use this in the except clauses
# - Thread 2 only appears to run once
# - Could you make the worker functions class based?
# - Could you add logging to standard out?
# - Could you add a timestamp to the logs?
# - Could logging be handed to a new separate thread?

# Configure logging to use a queue for asynchronous logging

logging_queue = Queue(-1)  # No limit on queue size
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(message)s',datefmt='%Y-%m-%d %H:%M:%S')

# Custom handler for logging to queue

class QueueHandler(logging.Handler):
    def emit(self, record):
        timestamp = time.ctime(time.time())
        dt = datetime.datetime.now()
        timestamp = dt.strftime("%d/%m/%y %H:%M:%S.%f")
        # logging.info( record ) 
        record.msg = f'{timestamp} {record.msg}'
        try:
            logging_queue.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)

# Remove all existing handlers and add our custom handler
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(QueueHandler())

fh = logging.FileHandler(f'{date_str}.log')
fh.setLevel(logging.DEBUG)
root_logger.addHandler(fh)

logging.debug('This message should go to the log file')
logging.info('So should this')
logging.warning('And this, too')
logging.error('And non-ASCII stuff, too, like Øresund and Malmö')

def log_consumer(close_logging):
    """
    Consumes log messages from the queue and prints them to stdout.
    This function runs in its own thread to handle logging asynchronously.
    """
    while not close_logging.is_set():
        try:
            record = logging_queue.get(timeout=0.1)
            print(record)   # Don't replace this with logging...
        except Empty:
            pass
    # Log a message when the thread is about to stop
    logging.info("Logging thread is shutting down")
    

class TickGenerator:
    def __init__(self, tick_queue, done_event, tick_delay = 60.0 / 48):
        self.tick_queue = tick_queue
        self.done_event = done_event
        self.tick_delay = tick_delay
        self.tick_count = 0 

    def run(self):
        """
        Generates a tick every second and places it into the tick_queue.
        """
        while not self.done_event.is_set():
            self.tick_count = self.tick_count + 1
            self.tick_queue.put(self.tick_count)
            
            delay = self.tick_delay - (time.time() % self.tick_delay)

            # logging.info('Generated a tick of %s secs with delay of %0.6f  Ticks: %s ' % (self.tick_delay, delay, self.tick_count))
            # logging.info('sleep delay:-%s' % (delay,))
                
            time.sleep(delay)  # Correct for drift, ensuring exact timing
     
     
class MIDIThread():
    """
                        import mido
                        msg = mido.Message('note_on', note=60)
                        msg.type
                        'note_on'
                        msg.note
                        60
                        msg.bytes()
                        [144, 60, 64]
                        msg.copy(channel=2)
                        Message('note_on', channel=2, note=60, velocity=64, time=0)
                        port = mido.open_output('Port Name')
                        port.send(msg)
                        with mido.open_input() as inport:
                            for msg in inport:
                                print(msg)
                        mid = mido.MidiFile('song.mid')
                        for msg in mid.play():
                        port.send(msg)
    """
    def __init__(self, thread_id, tick_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.tick_queue = tick_queue
        self.response_queue = response_queue
        self.done_event = done_event

        self.setup()
        
    def setup(self):
        MIDI_PORT = 'QmidiNet'
        self.tick_got_count = 0 
        self.tick_on = True
        self.ports = mido.get_output_names()
        logging.info('MIDI PORTS:-%s' % (self.ports,))
        
        for item in self.ports:
            print('ITEM:-,',item)
            if MIDI_PORT in item:
                port_name = item 
            else:
                logging.info(f'!! NO MIDI PORT !! :-{ MIDI_PORT }')
                
                
        logging.info(f'MIDI PORT:-{port_name} ')
        self.port = mido.open_output(port_name)
        
        self.midi_tick_on = mido.Message('note_on', channel = 2, note=60)
        self.midi_tick_off = mido.Message('note_off', channel = 2, note=60)
        
        
    def run(self):
        """
        Listens for ticks from the tick_queue, performs an action, and responds via response_queue.
        """
        
        while not self.done_event.is_set():
            try:
                tick = self.tick_queue.get(timeout=2)
                self.tick_got_count = self.tick_got_count + 1
                self.tick_on = not self.tick_on
                # if tick % beats_per_minute == 0:
                    # logging.info(f'Received tick: {tick}---count={self.tick_got_count}')
                
                if tick % beats_per_minute == 0 and self.tick_on:          # Clock Minute
                    #logging.info(f'clock minute tick {tick}')
                    if tick % (beats_per_hour) == 0:       # Clock Hour
                        logging.info('clock Hour %s %s        WHITE' %(tick, time.ctime(time.time())))
                        # WHITE      HOUR
                        self.response_queue.put(message["HOUR"])
                        
                    elif tick % (beats_per_hour) == (beats_per_hour / 4): # Clock Quarter
                        logging.info('clock Quarter %s %s     MAGENTA' %(tick, time.ctime(time.time())))
                        # MAGENTA      QUARTER
                        self.response_queue.put(message["QUARTER"])
                        
                    elif tick % (beats_per_hour) ==  beats_per_hour / 2: # Clock Half
                        logging.info('clock Half %s %s       GREEN' %(tick, time.ctime(time.time())))
                        # GREEN      HALF
                        self.response_queue.put(message["HALF"])       
                                        
                    elif tick % (beats_per_hour) == (beats_per_hour * 3) / 4: # Clock Three Quarter
                        logging.info('clock Three Quarter %s %s              CYAN' %(tick, time.ctime(time.time())))
                        # CYAN      THREE QUARTER
                        self.response_queue.put(message["THREE QUARTER"])                        
                    else:                                 # Clock Hour
                        logging.info('clock Minute %s %s                     RED' %(tick, time.ctime(time.time())))
                        # Red      CLOCK MINUTE
                        self.response_queue.put(message["MINUTE"])
                        
                        
                        
                        
                else:
                    if not self.tick_on:
                        self.port.send((self.midi_tick_on))
                        self.response_queue.put(message["TICK ON"])
                    else:
                        self.port.send((self.midi_tick_off))
                        self.response_queue.put(message["TICK OFF"])       #Blue Tick
                        
                # logging.info(f'Completed action for tick: {tick}')
                
                # Send response
                # self.response_queue.put(f'Thread {self.thread_id} completed action')
            except Empty:
                # logging.info(f'Thread {self.thread_id} did not receive a tick within timeout')
                continue
                
        self.shutdown()
       
    def shutdown(self):
       logging.info('MIDI Shutdown starting...')
       # self.encoder.writeRGBCode(0x000000)
       # self.encoder.writeGP1(self.laser_off) # Laser off
       # self.encoder.writeGP2(self.laser_off) # Lasers off
       logging.info('MIDI Shutdown cleanly...')
       
            
if __name__ == "__main__":
    last_distance = 0 
    
    # Setup communication queues
    logging.info('---------------------P Y T H O N     S T A R T ------------------------------------------------------------')
    tick_queue = Queue()
    # calibrate_queue = Queue()
    response_queue = Queue()
    # response_queue2 = Queue()
    # encoder_queue = Queue()
    
    # Event to signal threads to stop
    done_event = threading.Event()
    close_logging = threading.Event()

    # Create instances
    tick_gen = TickGenerator(tick_queue, done_event)
    
    MIDI_helper = MIDIThread(1, tick_queue, response_queue, done_event)
    
    # RGBEncoder_listener = EncoderListenThread(3, tick_queue, encoder_queue, done_event)
    # Calibrate_helper = MeasureThread(2, calibrate_queue, response_queue2, done_event)
    # Distance_helper = DistanceThread(4, tick_queue, response_queue1, done_event)

    # Create threads
    log_thread = threading.Thread(target=log_consumer, args=(close_logging,), name="LogConsumer")
    tick_generator = threading.Thread(target=tick_gen.run, name="TickGenerator")
    MIDI_consumer = threading.Thread(target=MIDI_helper.run, name=f"MIDI Thread-{1}")
    
    # rgb_encoder_listener = threading.Thread(target=RGBEncoder_listener.listen, name=f"RGBEncoder-listener Thread-{2}")
    # calibration_runner = threading.Thread(target=Calibrate_helper.calibrate, name=f"MeasureThread-{1}")

    # distance_generator = threading.Thread(target=Distance_helper.run, name="Distance_generator")

    # Start threads
    log_thread.start()
    tick_generator.start()
    MIDI_consumer.start()
    
    # rgb_encoder_listener.start()
    # calibration_runner.start()
    # distance_generator.start()


    try:
        while True:
            # Check responses from helper threads
            try:
                response = response_queue.get_nowait()
                if response:
                    response.consume()
                    
                    
                     # in message.keys():
                    # logging.info(f'MESSAGE: {response}')
                    
                # elif response == 'Start Calibrate':
                    # calibrate_queue.put('Run Calibrate')
                    
                # elif response.find('Distance') == 0 :
                    # distance = int(response1[9:])
                    # if distance_logging:
                        # print(' ' * (int(distance / 4)),'*', distance)
                    # if distance >= 8190:
                        # distance = last_distance
                    # pendulum_rrd.update(distance, 88)
                    # last_distance = distance
                else:
                    logging.info(f' ELSE Response {response}')
            except Empty:
                pass

            # try:
                # response2 = response_queue2.get_nowait()
                # logging.info(f'Response 1 {response2}') 

            # except Empty:
                # pass
                
            
            # try:
                # encoder_event = encoder_queue.get_nowait()
                # logging.info(f'RECIEVED Encoder Event {encoder_event}')
                # if "Encoder Double Pushed" in encoder_event:
                    # print('About to run calbrate after encoder double push')
                    # calibrate_queue.put('Run Calibrate')
                    # print('Have sent run calbrate after encoder double push')
                    
            # except Empty:
                # pass
            
            time.sleep(0.1)  # Small delay to reduce CPU usage
    
    except KeyboardInterrupt:
        logging.info("Stopping threads...")
        done_event.set()
        tick_generator.join()
        MIDI_consumer.join()
        # rgb_encoder_listener.join()
        # calibration_runner.join()
        # distance_generator.join()
        
        close_logging.set()
        log_thread.join()  # Wait for the log thread to finish
        logging.info("All threads have been stopped")
            
