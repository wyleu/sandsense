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

midi_file_overwrite = False     # Regenerate the MIDI Files.

laser_intensity = 200

beats_per_minute = 48   # 48   Sandbach church clock
beats_per_minute = 48 * 3  # 48   Sandbach church clock
beats_per_hour = beats_per_minute * 60 
beats_per_day = beats_per_hour * 24

ticks_per_beat   = 2

ticks_per_minute = beats_per_minute * ticks_per_beat
ticks_per_hour = beats_per_hour * ticks_per_beat
ticks_per_day = beats_per_day * ticks_per_beat

tick_delay = 60.0 / ticks_per_minute

tick_queue_size = 50
tick_queue_size_warn = 50 * 3 / 4 


class Message():
    def __init__(self, name, function):
        self.name = name
        self.function = function
        self.last_time = 0
        
    def __str__(self):
        return (f'MESSAGE: {self.name}')
        
    def consume(self):
        t = time.time()
        diff =  t - self.last_time
        self.last_time = t
        logging.info(f' {t} {self.name} {diff} freq: {1/diff}Hz  {response}') 

class TickOff(Message):
    def consume(self):
        pass
        
class BeatOff(Message):
    def consume(self):
        pass
        
message = {
        'HOUR' : Message('HOUR', None),
        'MINUTE' : Message('MINUTE', None),
        'HALF' : Message('HALF', None),
        'QUARTER' : Message('QUARTER', None),
        'THREE QUARTER' : Message('THREE QUARTER', None),
        'TICK ON' : TickOff('TICK ON', None),
        'TICK OFF' : TickOff('TICK OFF', None),
        'BEAT ON' : Message('BEAT ON', None),
        'BEAT OFF' : Message('BEAT OFF', None),        
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
    def __init__(self, beat_queue, tick_monitor_queue,  tick_control_queue, done_event):
        self.beat_queue = beat_queue
        self.tick_control_queue = tick_control_queue
        self.tick_monitor_queue = tick_monitor_queue
        self.done_event = done_event
        self.tick_count = 0 
        self.beat_count = 0 

    def run(self):
        logging.info(f'TICKS PER BEAT:-{ ticks_per_beat }')
        while not self.done_event.is_set():
            self.tick_monitor_queue.put(self.tick_count)
            
            if self.tick_count % ticks_per_beat == 0:
                self.beat_queue.put(self.beat_count)
                logging.info(f'self.beat_Count {self.beat_count}')
                self.beat_count = self.beat_count + 1
                
            delay = tick_delay - (time.time() % tick_delay)

            #logging.info('Generated a tick of %s secs with delay of %0.6f  Ticks: %s ' % (tick_delay, delay, self.tick_count))
            #logging.info('sleep delay:-%s' % (delay,))
                
            time.sleep(delay)  # Correct for drift, ensuring exact timing
            self.tick_count = self.tick_count + 1

class ClockThread():
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
    def __init__(self, thread_id, beat_queue, chime_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.beat_queue = beat_queue
        self.response_queue = response_queue
        self.chime_queue = chime_queue
        self.done_event = done_event

        self.setup()

        
    def setup(self):
        MIDI_PORT = 'QmidiNet'
        self.beat_got_count = 0 
        self.beat_on = True
        
        self.chimes = [ 
                {'QUARTER': 1},
                {'HALF': 2},
                {'THREE QUARTER': 3}, 
                {'HOUR': 4},               
            ]
            
        self.ports = mido.get_output_names()
        logging.info('MIDI PORTS:-%s' % (self.ports,))
        logging.info(f'Made MIDI Files')
        
        for item in self.ports:
            print('ITEM:-,',item)
            if MIDI_PORT in item:
                port_name = item 
                break
            else:
                logging.info(f'!! NO MIDI PORT !! :-{ MIDI_PORT } in {item}')
                
        logging.info(f'MIDI PORT:-{port_name} ')
        self.port = mido.open_output(port_name)
        
        self.midi_beat_on = mido.Message('note_on', channel = 2, note=60, velocity = 30)
        self.midi_beat_off = mido.Message('note_off', channel = 2, note=60)
        self.midi_beat_minute = mido.Message('note_on', channel = 2, note=60, velocity = 80)
        self.midi_beat_hour = mido.Message('note_off', channel = 2, note=60, velocity = 100)
        self.midi_beat_quarter = mido.Message('note_off', channel = 2, note=60, velocity = 50)
        self.midi_beat_half = mido.Message('note_off', channel = 2, note=60, velocity = 60)
        self.midi_beat_three_quarter = mido.Message('note_off', channel = 2, note=60, velocity = 70)
        
    def run(self):
        """
        Listens for ticks from the beat_queue, performs an action, and responds via response_queue.
        """
        
        while not self.done_event.is_set():
            try:
                beat = self.beat_queue.get(timeout=2)
                self.beat_got_count = self.beat_got_count + 1
                self.beat_on = not self.beat_on
                # logging.info(f'.................................................Received tick: {beat}---count={self.beat_got_count}')
                
                if self.beat_on:
                    self.port.send((self.midi_beat_on))
                    self.response_queue.put(message["BEAT ON"])
                else:
                    self.port.send((self.midi_beat_off))
                    self.response_queue.put(message["BEAT OFF"])       # Blue beat
                
                
                # if beat % beats_per_minute == 0:
                #    logging.info(f'Received tick: {beat}---count={self.beat_got_count}')
                
                if beat % beats_per_minute == 0 and self.beat_on:          # Clock Minute
                    logging.info(f'-------------------------------clock minute beat {beat}')
                    if beat % (beats_per_hour) == 0:       # Clock Hour
                        #logging.info('clock Hour %s %s        WHITE' %(beat, time.ctime(time.time())))
                        # WHITE      HOUR
                        chime = 'HOUR'
                        self.port.send((self.midi_beat_hour))
                        self.response_queue.put(message[chime])
                        self.chime_queue.put(chime)
                        
                    elif beat % (beats_per_hour) == (beats_per_hour / 4): # Clock Quarter
                        #logging.info('clock Quarter %s %s     MAGENTA' %(beat, time.ctime(time.time())))
                        # MAGENTA      QUARTER
                        chime = "QUARTER"

                        self.port.send((self.midi_beat_quarter))
                        self.response_queue.put(message[chime])
                        self.chime_queue.put(chime)
                        
                    elif beat % (beats_per_hour) ==  beats_per_hour / 2: # Clock Half
                        #logging.info('clock Half %s %s       GREEN' %(beat, time.ctime(time.time())))
                        # GREEN      HALF
                        chime = "HALF"
                        self.port.send((self.midi_beat_half))
                        self.response_queue.put(message["HALF"]) 
                        self.chime_queue.put(chime)  
                                        
                    elif beat % (beats_per_hour) == (beats_per_hour * 3) / 4: # Clock Three Quarter
                        #logging.info('clock Three Quarter %s %s              CYAN' %(beat, time.ctime(time.time())))
                        # CYAN      THREE QUARTER
                        
                        chime = "THREE QUARTER"
                        self.port.send((self.midi_beat_three_quarter))
                        self.response_queue.put(message["THREE QUARTER"]) 
                        self.chime_queue.put(chime)
                                                
                    else:                                                     # Clock Minute
                        #logging.info('clock Minute %s %s                     RED' %(beat, time.ctime(time.time())))
                        # Red      CLOCK MINUTE
                        self.port.send((self.midi_beat_minute))
                        self.response_queue.put(message["MINUTE"])
                        
                else:
                    pass
                        
                # logging.info(f'Completed action for beat: {beat}')
                
                # Send response
                # self.response_queue.put(f'Thread {self.thread_id} completed action')
            except Empty:
                # logging.info(f'Thread {self.thread_id} did not receive a beat within timeout')
                continue
                
        self.shutdown()
       
    def shutdown(self):
       logging.info('MIDI Shutdown starting...')
       # self.encoder.writeRGBCode(0x000000)
       # self.encoder.writeGP1(self.laser_off) # Laser off
       # self.encoder.writeGP2(self.laser_off) # Lasers off
       logging.info('MIDI Shutdown cleanly...')
      
      
class ChimeThread(ClockThread):
    
    def __init__(self, thread_id, chime_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.chime_queue = chime_queue
        self.response_queue = response_queue
        self.done_event = done_event
        midi_file_overwrite = True

        self.setup()
        self.make_midi_files()                
        
    def run(self):
        while not self.done_event.is_set():
            try:
                chime = self.chime_queue.get(timeout=2)
                logging.info(f'playing {chime}')
                self.play_midi_chime_file(chime)
                logging.info(f'played {chime}')
                
            except Empty:
                # logging.info(f'Thread {self.thread_id} did not receive a beat within timeout')
                continue
                
            time.sleep(.4)
                
    def get_pattern(self, midi_patterns):
        for item in midi_patterns:
            yield item 
        
    def make_midi_files(self,
                        programme_change = 12, 
                        midi_channel = 11,                
                        root_note = 53,         # Low F on 53
                        note_duration = 0.5     # seconds
                        ):
                            

        self.bell_patterns = [           # Bell numbers
                ( 2, 3, 4, 7),
                ( 4, 2, 3, 7),
                ( 4, 3, 2, 4),
                ( 2, 4 ,3, 7),
                ( 7, 3, 2, 4),
                ]
                
        self.midi_patterns = [
                ( root_note + 11, root_note + 9, root_note + 7, root_note + 2),
                ( root_note + 7, root_note + 11, root_note + 9, root_note + 2),
                ( root_note + 7, root_note + 9, root_note + 11, root_note + 7),
                ( root_note + 11, root_note + 7 ,root_note + 9, root_note + 2),
                ( root_note + 2, root_note + 9, root_note + 11, root_note + 7),                
        ]

        chime_count = 1
        pattern = 0
        
        for chime_dict in self.chimes:
            for chime in chime_dict:  # Get a dict key
                print(f'MAKING A MIDI FILE....{chime} WITH CHIME COUNT OF: {chime_count}')
                midi_file_name = self.make_file_name(chime)
                if not self.check_for_midi_file(midi_file_name):
                    # Make MIDI File
                    
                    mid = mido.MidiFile()
                    track = mido.MidiTrack()
                    mid.tracks.append(track)            
                    track.append(mido.Message('program_change', program=12, channel = midi_channel, time=0))
                    
                    for count in range(chime_count):
                        for midi_note in self.get_pattern(self.midi_patterns[pattern]):
                            print(f'MIDI_NOTE: {midi_note} CHIME: {chime}')
                            track.append(mido.Message('note_on', channel = midi_channel, note = midi_note, time =  32))
                        pattern = pattern + 1
                        if pattern == 5:
                            pattern = 0 
                            
                    mid.save(midi_file_name)
            
            chime_count = chime_count + 1
            
    def make_file_name(self, chime):
        return f'{chime}.mid'
        
    def play_midi_chime_file(self, chime):
        filename = self.make_file_name(chime)
        
        for msg in mido.MidiFile(filename):
            logging.info(f' {msg}')
            time.sleep(msg.time * 24)    # Pure guess    Ticks per quarter note in MIDI files
            if not msg.is_meta:
                self.port.send(msg)
        
    def check_for_midi_file(self, name):
        if midi_file_overwrite:
            return False
        return os.path.exists(name)

class MonitorThread(ClockThread):
    def __init__(self, thread_id, tick_monitor_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.tick_monitor_queue = tick_monitor_queue
        self.response_queue = response_queue
        self.done_event = done_event
        
        self.setup()    # load up the MIDI bits
        
        GPIO.setmode(GPIO.BCM)
        self.bus = smbus2.SMBus(1)
        self.INT_pin = 17
        
        self.laser_setup()
        self.atod_setup()
        
    def laser_setup(self):
        pass
        # Manage the lasers 
        
        self.laser_bright = 0
        self.laser_off = 255
        self.laser_dim = 245
        self.laser_brightish = 245
        
        #Encoder set up

        GPIO.setup(self.INT_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.encoder = i2cEncoderLibV2.i2cEncoderLibV2(self.bus, 0x47)
    
        encconfig = (
            i2cEncoderLibV2.INT_DATA |
            i2cEncoderLibV2.WRAP_ENABLE |
            i2cEncoderLibV2.DIRE_RIGHT |
            i2cEncoderLibV2.IPUP_ENABLE |
            i2cEncoderLibV2.RMOD_X1 |
            i2cEncoderLibV2.RGB_ENCODER)
    
        self.encoder.begin(encconfig)
    
        self.encoder.writeCounter(0)
        self.encoder.writeMax(35)
        self.encoder.writeMin(00)
        self.encoder.writeStep(1)
        self.encoder.writeAntibouncingPeriod(8)
        self.encoder.writeDoublePushPeriod(50)
    
        self.encoder.writeGammaRLED(i2cEncoderLibV2.GAMMA_2)
        self.encoder.writeGammaGLED(i2cEncoderLibV2.GAMMA_2)
        self.encoder.writeGammaBLED(i2cEncoderLibV2.GAMMA_2)
    
        self.encoder.writeGP1conf(i2cEncoderLibV2.GP_PWM |
            i2cEncoderLibV2.GP_PULL_DI |
            i2cEncoderLibV2.GP_INT_DI
        )
    
        self.encoder.writeGP2conf(i2cEncoderLibV2.GP_PWM|
             i2cEncoderLibV2.GP_PULL_DI |
             i2cEncoderLibV2.GP_INT_DI
        )
        
    
        self.encoder.onChange = self.EncoderChange
        self.encoder.onButtonPush = self.EncoderPush
        self.encoder.onButtonDoublePush = self.EncoderDoublePush
        self.encoder.onMax = self.EncoderMax
        self.encoder.onMin = self.EncoderMin
    
        self.encoder.autoconfigInterrupt()
    
        logging.info ('Board ID code: 0x%X' % (self.encoder.readIDCode()))
        logging.info ('Board Version: 0x%X' % (self.encoder.readVersion()))
        
        self.encoder.writeRGBCode(0x640000)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x006400)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x000064)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x00)
        
        self.encoder.writeGP1(self.laser_brightish) # Side Lasers on   255 off 0 full brightness
        self.encoder.writeGP2(self.laser_brightish) # Front Laser on
    
        logging.info('Laser1 %s'% (self.encoder.readGP1(),))
        logging.info('Laser2 %s' % (self.encoder.readGP2(),))
    
        logging.info('Laser1 conf %s' % (self.encoder.readGP1conf(),))
        logging.info('Laser2 conf %s' % (self.encoder.readGP2conf(),))
        
        self.encoder.writeGP1(255) # Side Lasers off   255 off 0 full brightness
        self.encoder.writeGP2(255) # Front Laser off
        
    def atod_setup(self):
        self.average_list = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]   # yuck
        # Manage the LDR's
        logging.info('...............................................................Starting up AtoD')
        # Set up the AtoD board.
        self.ADS = ADS1x15.ADS1115(1,0x4a)
        logging.info("ADS:- %s" % (self.ADS,))
        self.ADS.setGain(self.ADS.PGA_4_096V)

    def display(self):
        logging.info("ADS getMaxVolts:- %s" % (self.ADS.getMaxVoltage(),))
        logging.info("ADS getGain:- %s" % (self.ADS.getGain(),))
        logging.info("ADS.ADC0:- %s" % (self.ADS.readADC(0),))
        logging.info("ADS.ADC1:-%s" % (self.ADS.readADC(1),))
        logging.info("ADS.ADC2:-%s" % (self.ADS.readADC(2),))
        logging.info("ADS.ADC3:-%s" % (self.ADS.readADC(3),))
        logging.info("ADS getMaxVolts:-%s" % (self.ADS.getMaxVoltage(),))
        logging.info("ADS getGain:-%s" % (self.ADS.getGain(),))
        
    def shutdown(self):
       logging.info('Encoder Shutdown starting...')
       self.encoder.writeRGBCode(0x000000)
       self.encoder.writeGP1(self.laser_off) # Side Lasers off
       self.encoder.writeGP2(self.laser_off) # Front Lasers off
       logging.info('Encoder Shutdown cleanly...')
       
        
    def EncoderChange(self):
        self.encoder.writeLEDG(100)
        self.response_queue.put(f'Thread {self.thread_id} Changed: {self.encoder.readCounter32()}')
        self.encoder.writeLEDG(0)
    
    def EncoderPush(self):
        #self.encoder.writeLEDB(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Pushed')
        logging.info ('Encoder Pushed!')
        #self.encoder.writeLEDB(0)
    
    def EncoderDoublePush(self):
        #self.encoder.writeLEDB(100)
        #self.encoder.writeLEDG(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Double Pushed')
        logging.info ('Encoder Double Push!')
        #self.encoder.writeLEDB(0)
        #self.encoder.writeLEDG(0)
    
    def EncoderMax(self):
        #self.encoder.writeLEDR(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Max')
        logging.info ('Encoder max!')
        #self.encoder.writeLEDR(0)
    
    def EncoderMin(self):
        #self.encoder.writeLEDR(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Min')
        logging.info ('Encoder min!')
        #self.encoder.writeLEDR(0)
    
    def Encoder_INT(self):
        self.encoder.updateStatus()
        
    def calculate_average(self, value):
        
        total = 0 
        self.average_list.pop()
        self.average_list.insert(0, value )
        
        for item in self.average_list:
            total = total + item
        
        return total / len(self.average_list)
        
    def run(self):
        self.laser_on = True
        self.average_light_level = 0 
        
        while not self.done_event.is_set():
            try:
                tick = self.tick_monitor_queue.get(timeout=2)
                # write lasers and read ldrs 
                
                if self.tick_monitor_queue.qsize() > tick_queue_size_warn:
                    logging.info(f'------------------------------------------queue length:{self.tick_monitor_queue.qsize()} tick {tick} a0 {self.a0}, a 1 {self.a1} Laser { self.laser_on}    QUEUE FILING!!!!') # , self.a2, self.a3)
                
                for i in range(2):
                    self.a0 = self.ADS.readADC(0)    # front
                    self.a1 = self.ADS.readADC(1)    # front
                    # self.a2 = self.ADS.readADC(2)    # side
                    # self.a3 = self.ADS.readADC(3)    # side
                    

                    
                    self.m0 = map_range(self.a0, 20000, 29000, 1, 127)
                    self.m1 = map_range(self.a1, 20000, 29000, 1, 127)
                    #self.m2 = map_range(self.a2, 1, 32768, 1, 127)
                    #self.m3 = map_range(self.a3, 1, 32768, 1, 127)
                    
                    self.port.send(mido.Message(type='control_change',control = 100 + i, value = self.m0))
                    self.port.send(mido.Message(type='control_change',control = 100 + i, value = self.m1))
                    #self.port.send(mido.Message(type='control_change',control = 100 + i, value = self.m2))
                    #self.port.send(mido.Message(type='control_change',control = 100 + i, value = self.m3))
                    
                    if self.laser_on: 
                        self.encoder.writeGP1(255) # Side Lasers off   255 off 0 full brightness
                        self.encoder.writeGP2(255) # Front Laser off
                    else:
                        # self.encoder.writeGP1(laser_intensity) # Side Laser off   255 off 0 full brightness
                        self.encoder.writeGP2(laser_intensity) # Front Laser off
                    self.laser_on = not self.laser_on
                    
                    # time.sleep(.02)
                        
            except self.tick_monitor_queue.full:
                logging.info(f'-----------------------------------------------------{self.tick_monitor_queue.qsize()} Tick THREAD FULL!!!!!!!!')
            except self.tick_monitor_queue.Empty:
                logging.info(f'Thread {self.thread_id} did not receive a tick within timeout')
                # continue
            except Exception as e:
                logging.info(f'Exception: {e}')
                
                
            time.sleep(.1)
            
        self.shutdown()
        
        
if __name__ == "__main__":
    last_distance = 0 
    
    # Setup communication queues
    logging.info('---------------------P Y T H O N     S T A R T ------------------------------------------------------------')
    beat_queue = Queue()
    tick_monitor_queue = Queue(tick_queue_size)
    # calibrate_queue = Queue()
    response_queue = Queue()
    chime_queue = Queue()
    tick_control_queue = Queue()
    
    
    # response_queue2 = Queue()
    # encoder_queue = Queue()
    
    # Event to signal threads to stop
    done_event = threading.Event()
    close_logging = threading.Event()

    # Create instances
    tick_gen = TickGenerator(beat_queue,tick_monitor_queue, tick_control_queue, done_event)
    MIDI_helper = ClockThread(1, beat_queue, chime_queue, response_queue, done_event)
    chain_helper = ChimeThread(2, chime_queue, response_queue, done_event)
    monitor_helper = MonitorThread(3, tick_monitor_queue, response_queue, done_event)
    
    # RGBEncoder_listener = EncoderListenThread(3, beat_queue, encoder_queue, done_event)
    # Calibrate_helper = MeasureThread(2, calibrate_queue, response_queue2, done_event)
    # Distance_helper = DistanceThread(4, beat_queue, response_queue1, done_event)

    # Create threads
    log_thread = threading.Thread(target=log_consumer, args=(close_logging,), name="LogConsumer")
    tick_generator = threading.Thread(target=tick_gen.run, name="TickGenerator")
    MIDI_consumer = threading.Thread(target=MIDI_helper.run, name=f"MIDI Consumer")
    chime_consumer = threading.Thread(target=chain_helper.run, name= "chain_consumer")
    monitor_generator = threading.Thread(target=monitor_helper.run, name="monitor helper")
    
    # rgb_encoder_listener = threading.Thread(target=RGBEncoder_listener.listen, name=f"RGBEncoder-listener Thread-{2}")
    # calibration_runner = threading.Thread(target=Calibrate_helper.calibrate, name=f"MeasureThread-{1}")

    # distance_generator = threading.Thread(target=Distance_helper.run, name="Distance_generator")

    # Start threads
    log_thread.start()
    tick_generator.start()
    MIDI_consumer.start()
    chime_consumer.start()
    monitor_generator.start()
    
    # rgb_encoder_listener.start()
    # calibration_runner.start()
    # distance_generator.start()
    
    logging.info(f'TICKS PER BEAT {ticks_per_beat}')
    logging.info(f'Beats Per Minute:{beats_per_minute}    Beats per Hour:{beats_per_hour} Beats per Day:{beats_per_day}')
    logging.info(f'Ticks Per Min:{ticks_per_minute}     Ticks per Hour: {ticks_per_hour}  Ticks per Day:{ ticks_per_day }')
    logging.info (f'TICK DELAY:-{tick_delay}')


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
            
