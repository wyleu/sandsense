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

rrdfile = "rrdfile-5.rrd"
rrdpendulum = "pendulum.rrd"

date_str = datetime.datetime.now().strftime("Logfile_%Y:%m:%d:%H:%M:%S")

distance_logging = False

beats_per_minute = 48
ticks_per_beat   = 24

ticks_per_minute = beats_per_minute * ticks_per_beat
tick_delay = 60.0 / ticks_per_minute
beats_per_hour = beats_per_minute * 60 
ticks_per_hour = beats_per_hour * ticks_per_beat
ticks_per_day = beats_per_hour * ticks_per_beat * 24

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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(message)s',datefmt='%Y-%m-%d %H:%M:%S.%f')

# Custom handler for logging to queue

class QueueHandler(logging.Handler):
    def emit(self, record):
        date_str = datetime.datetime.now().strftime("%Y:%m:%d:%H:%M:%S.%f")
        # timestamp = time.ctime(time.time())
        # logging.info( record ) 
        record.msg = f'{date_str} {record.msg}'
        try:
            logging_queue.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)

# Remove all existing handlers and add our custom handler
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(QueueHandler())

fh = logging.FileHandler(f'logs/{date_str}.log')
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
class SpectrumAnalyser:
    def __init__(self):
        pass
        
    def run(self):
        pass
        
class TickGenerator:
    def __init__(self, tick_queue, trigger_queue, done_event, tick_delay):
        self.tick_queue = tick_queue
        self.trigger_queue = trigger_queue
        self.done_event = done_event
        self.tick_delay = tick_delay
        self.tick_count = 0
        self.midnight_flag = False 

    def run(self):
        """
        Generates a tick and places it into the tick_queue.
        """
        while not self.done_event.is_set():
            self.tick_count = self.tick_count + 1
            if self.tick_count == ticks_per_day:
                self.tick_count = 0
                logging.info(f' Tick Reset-')

            if (time.time() % 86400) == 0 and self.midnight_flag == False:
                # Midnight
                self.midnight_flag = True
                logging.info(f' Midnight Reset- {self.tick_count}')
            elif self.midnight_flag == True:
                pass
                
            self.tick_queue.put(self.tick_count)
            self.trigger_queue.put('Distance_request')
            
            delay = self.tick_delay - (time.time() % self.tick_delay)

            # logging.info('Generated a tick of %s secs with delay of %0.6f  Ticks: %s ' % (self.tick_delay, delay, self.tick_count))
            time.sleep(delay)  # Correct for drift, ensuring exact timing
            
class RGBEncoderThread():
    def __init__(self, thread_id, tick_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.tick_queue = tick_queue
        self.response_queue = response_queue
        self.done_event = done_event

        self.setup()
        
    def setup(self):
        GPIO.setmode(GPIO.BCM)
        bus = smbus2.SMBus(1)
        
        # Output Laser
        
        self.laser_bright = 0
        self.laser_off = 255
        self.laser_dim = 245
        self.laser_brightish = 245
        
                
        self.tick_LED = True
        self.tick_got_count = 0 
        
        #Encoder set up
    
        self.INT_pin = 17
        GPIO.setup(self.INT_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
        self.encoder = i2cEncoderLibV2.i2cEncoderLibV2(bus, 0x47)
    
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
        
        self.encoder.writeGP1(self.laser_brightish) # Laser on   255 off 0 full brightness
        self.encoder.writeGP2(self.laser_brightish) # Lasers on
    
        logging.info('Laser1 %s'% (self.encoder.readGP1(),))
        logging.info('Laser2 %s' % (self.encoder.readGP2(),))
    
        logging.info('Laser1 conf %s' % (self.encoder.readGP1conf(),))
        logging.info('Laser2 conf %s' % (self.encoder.readGP2conf(),))
        
        self.encoder.writeGP1(255) # Laser off   255 off 0 full brightness
        self.encoder.writeGP2(255) # Lasers off
        
    def drive(self):
        while not self.done_event.is_set():
            try:
                tick = self.tick_queue.get(timeout=2)
            except Empty:
                logging.info(f'Thread {self.thread_id} did not receive a tick within timeout')
                continue
                
        self.shutdown()
        
        
    def run(self):
        """
        Listens for ticks from the tick_queue, performs an action, and responds via response_queue.
        """
        
        while not self.done_event.is_set():
            try:
                tick = self.tick_queue.get(timeout=2)
                self.tick_got_count = self.tick_got_count + 1

                # if tick % ticks_per_minute == 0:
                # logging.info(f'Ticks per minute {ticks_per_minute} Received tick: {tick}---count={self.tick_got_count}')
                    
                if tick % ticks_per_minute == 0:          # Clock Minute
                    #logging.info(f'clock minute tick {tick}')
                    if tick % (ticks_per_hour) == 0:       # Clock Hour
                        logging.info('clock Hour %s         WHITE' %(tick,))
                        self.encoder.writeLEDR(100)         # WHITE      HOUR
                        self.encoder.writeLEDB(100)
                        self.encoder.writeLEDG(100)
                        self.response_queue.put("Start_Calibrate")
                        
                    elif tick % (ticks_per_hour) == (ticks_per_hour / 4): # Clock Quarter
                        logging.info('clock Quarter %s      MAGENTA' %(tick,))
                        self.encoder.writeLEDR(100)         # MAGENTA      QUARTER
                        self.encoder.writeLEDB(100)
                        self.encoder.writeLEDG(0)
                        self.response_queue.put("Start_Calibrate")
                        
                    elif tick % (ticks_per_hour) ==  ticks_per_hour / 2: # Clock Half
                        logging.info('clock Half %s        GREEN' %(tick,))
                        self.encoder.writeLEDR(0)           # GREEN      HALF
                        self.encoder.writeLEDB(0)
                        self.encoder.writeLEDG(100)
                        self.response_queue.put("Start_Calibrate")       
                                        
                    elif tick % (ticks_per_hour) == (ticks_per_hour * 3) / 4: # Clock Three Quarter
                        logging.info('clock Three Quarter %s               CYAN' %(tick,))
                        self.encoder.writeLEDR(0)           # CYAN      THREE QUARTER
                        self.encoder.writeLEDB(100)
                        self.encoder.writeLEDG(100)
                        self.response_queue.put("Start_Calibrate")                        
                    else:                                 # Clock Hour
                        logging.info('clock Minute %s                       RED' %(tick,))
                        self.encoder.writeLEDR(100)         # Red      CLOCK MINUTE
                        self.encoder.writeLEDB(0)
                        self.encoder.writeLEDG(0)
                        
                        
                    self.tick_LED = not self.tick_LED
                        
                elif tick % ticks_per_beat == 0:
                    if not self.tick_LED:
                        self.encoder.writeRGBCode(0x000000)
                    else:
                        self.encoder.writeRGBCode(0x000064)       #Blue Tick
                        
                    self.tick_LED = not self.tick_LED
                        
                # logging.info(f'Completed action for tick: {tick}')
                
                # Send response
                # self.response_queue.put(f'Thread {self.thread_id} completed action')
            except Empty:
                # logging.info(f'Thread {self.thread_id} did not receive a tick within timeout')
                continue
                
        self.shutdown()
       
    def shutdown(self):
       logging.info('Encoder Shutdown starting...')
       self.encoder.writeRGBCode(0x000000)
       self.encoder.writeGP1(self.laser_off) # Laser off
       self.encoder.writeGP2(self.laser_off) # Lasers off
       logging.info('Encoder Shutdown cleanly...')
       
        
    def EncoderChange(self):
        #self.encoder.writeLEDG(100)
        self.response_queue.put(f'Thread {self.thread_id} Changed: {self.encoder.readCounter32()}')
        #self.encoder.writeLEDG(0)
    
    def EncoderPush(self):
        #self.encoder.writeLEDB(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Pushed')
        #logging.info ('Encoder Pushed!')
        #self.encoder.writeLEDB(0)
    
    def EncoderDoublePush(self):
        #self.encoder.writeLEDB(100)
        #self.encoder.writeLEDG(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Double Pushed')
        #logging.info ('Encoder Double Push!')
        #self.encoder.writeLEDB(0)
        #self.encoder.writeLEDG(0)
    
    def EncoderMax(self):
        #self.encoder.writeLEDR(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Max')
        #logging.info ('Encoder max!')
        #self.encoder.writeLEDR(0)
    
    def EncoderMin(self):
        #self.encoder.writeLEDR(100)
        self.response_queue.put(f'Thread {self.thread_id} Encoder Min')
        #logging.info ('Encoder min!')
        #self.encoder.writeLEDR(0)
    
    def Encoder_INT(self):
        self.encoder.updateStatus()

class AtoD():
    def __init__(self):
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
        
class LDR():
    def __init__(self,laser, atod, sensor = 0):
        self.name = f'LDR {sensor}'
        self.sensor = sensor
        self.atod = atod
        self.value = 0
        self.last_value = 0
        self.laser = laser
        self.read_count = 0
        self.a0 = 0
        self.a1 = 0
        self.a2 = 0
        self.a3 = 0
        self.average = 0
        self.setup()

    def setup(self):
        # Define thresholds.
        self.no_of_reads = 0
        
        self.light_minimum = 0
        self.dark_maximum = 0 
        
        self.upper_threshold = 0
        self.threshold = 0 
        self.lower_threshold = 0 
        
        self.dark_minimum = 1000000 
        self._light_minimum = 1000000
        
        # Events
        self.rising= False
        self.descending = False
        self.maximum_set = False
        self.pos_upper_threshold = False
        self.neg_upper_threshold = False
        self.pos_threshold = False
        self.neg_threshold = False        
        self.pos_lower_threshold = False
        self.neg_lower_threshold = False 
        self.minimum_set = False
        
    def read(self):
        self.read_all()
        self.no_of_reads = self.no_of_reads + 1
        
        self.last_value = self.value
        self.last_average = self.average
               
        if self.sensor == 0:
            self.value = self.a0
        if self.sensor == 1:
            self.value = self.a1
        if self.sensor == 2:
            self.value =  self.a2
        if self.sensor == 3:
            self.value = self.a3
            
        if self.last_value > self.value:   # Light Level Dropping 
            self.descending = True 
            self.rising = False
            
        if self.last_value < self.value:   # Light Level Rising 
            self.descending = False 
            self.rising = True
            
        if self.last_value == self.value:   # Light Level No Change 
            self.descending = False 
            self.rising = False
            
        return self.value

    def read_all(self):
        self.a0_prev = self.a0
        self.a1_prev = self.a1
        self.a2_prev = self.a2
        self.a3_prev = self.a3
        self.av_prev = self.average
        
        self.a0 = self.atod.ADS.readADC(0)
        self.a1 = self.atod.ADS.readADC(1)
        self.a2 = self.atod.ADS.readADC(2)
        self.a3 = self.atod.ADS.readADC(3)

        self.average = ( self.a0 + self.a1 + self.a2 + self.a3 ) / 4
        
        return(self.a0, self.a1, self.a2, self.a3, self.average)
        
    def get_average(self):
        return self.average
        
    def display(self):
        logging.info("%s  %s  %s  %s  %s  %s" % (a0, a1, a2, a3,  self.distance , map_range(self.distance, 0, 1290, 0, 64)))
    
    def take_reading(self, times = 5, sleep_time = 0.001):
        # Do the lasery on and off stuff...
        dark_reading = 0
        light_reading = 0 
        dark_total = 0
        light_total = 0 
        dark_max = 0 
        dark_min = 1000000
        light_max = 0
        light_min = 1000000 
        
        for i in range(times):
            # if i == 1:
                # logging.info('%s TESTS OF LENGTH %s Seconds on %s: DARK=%s, LIGHT=%s, DIFFERENCE = %s' % (times, sleep_time, self.name, dark_reading, light_reading, light_reading - dark_reading))
            self.laser.laser_off()
            time.sleep(sleep_time)
            dark_reading = self.read()
            self.read_count = self.read_count + 1
            
            dark_total = dark_total + dark_reading
            if dark_min > dark_reading:
                dark_min = dark_reading
            if dark_max < dark_reading:
                dark_max = dark_reading
                
            self.laser.laser_on(240)
            time.sleep(sleep_time)
            light_reading = self.read()
            self.read_count = self.read_count + 1
            light_total = light_total + light_reading
            if light_min > light_reading:
                light_min = light_reading
            if light_max < light_reading:
                light_max = light_reading
            time.sleep(sleep_time)
            self.laser.laser_off()
            
        dark_average = dark_total / times
        light_average = light_total / times

        threshold = round(dark_average + ((light_average-dark_average))/2)
        diff = round(light_average - dark_average)

        #logging.info(f'{self.name} | {dark_min} {dark_average} {dark_max}  > {threshold } < {light_min}  {light_average}  {light_max}  |  {light_max-dark_min}  {round(light_average-dark_average,2)}  {light_min-dark_max}  | {sleep_time}')
        # if self.read_count % 20 == 0:
            # print(datetime.datetime.now().strftime("%Y:%m:%d:%H:%M:%S.%f"),' ' * (int(light_average - dark_average / 500)),'*', light_average, dark_average)
        return (dark_average, light_average, diff , dark_min, dark_max, threshold, light_min, light_max, times, sleep_time)
            
class Laser():
    def __init__(self, RGBEncoder, number):
        self.number = number
        self.RGBEncoder = RGBEncoder
        self.setup()
        
    def setup(self):
        logging.info('Laser instance created')
        pass

    def laser_on(self, brightness = 240):
        if self.number == 1:
            self.RGBEncoder.writeGP1(brightness)
        if self.number == 2:
            self.RGBEncoder.writeGP2(brightness)
        
    def laser_off(self):
        if self.number == 1:
            self.RGBEncoder.writeGP1(255)
        if self.number == 2:
            self.RGBEncoder.writeGP2(255)

    def all_laser_off(self):
        self.RGBEncoder.writeGP1(255)
        self.RGBEncoder.writeGP2(255)
        
class Rrd():
    def __init__(self, filename_base):
        self.filename="Default.rrd"
        self.create_rrdfile()

        
    def create_rrdfile(self):
        big_data_sources=[
         'DS::COUNTER:600:U:U',
         'DS:leftside_dark_min:GAUGE:600:U:U',
         'DS:leftside_dark_max:GAUGE:600:U:U',
         'DS:leftside_dark_total:GAUGE:600:U:U',
         'DS:leftside_light_min:GAUGE:600:U:U',
         'DS:leftside_light_max:GAUGE:600:U:U',
         'DS:leftside_light_total:GAUGE:600:U:U',
         'DS:rightside_dark_min:GAUGE:600:U:U',
         'DS:rightside_dark_max:GAUGE:600:U:U',
         'DS:rightside_dark_total:GAUGE:600:U:U',
         'DS:rightside_light_min:GAUGE:600:U:U',
         'DS:rightside_light_max:GAUGE:600:U:U',
         'DS:rightside_light_total:GAUGE:600:U:U',
         'DS:rightfront_dark_min:GAUGE:600:U:U',
         'DS:rightfront_dark_max:GAUGE:600:U:U',
         'DS:rightfront_dark_total:GAUGE:600:U:U',
         'DS:rightfront_light_min:GAUGE:600:U:U',
         'DS:rightfront_light_max:GAUGE:600:U:U',
         'DS:rightfront_light_total:GAUGE:600:U:U',
         'DS:leftfront_dark_min:GAUGE:600:U:U',
         'DS:leftfront_dark_max:GAUGE:600:U:U',
         'DS:leftfront_dark_total:GAUGE:600:U:U',
         'DS:leftfront_light_min:GAUGE:600:U:U',
         'DS:leftfront_light_max:GAUGE:600:U:U',
         'DS:leftfront_light_total:GAUGE:600:U:U',
         'DS:distance_min:GAUGE:600:U:U',
         'DS:distance_max:GAUGE:600:U:U',
         'DS:ticks_in_day:GAUGE:600:U:U',
         'DS:temperature_internal:GAUGE:600:U:U',
         'DS:temperature_external_1:GAUGE:600:U:U',
         'DS:temperature_external_2:GAUGE:600:U:U',
         'DS:temperature_external_3:GAUGE:600:U:U',
         'DS:temperature_external_4:GAUGE:600:U:U',
        ]
        
        small_data_sources = [
         'DS:leftfront_light_min:GAUGE:600:U:U',
         'DS:leftfront_light_max:GAUGE:600:U:U',
         'DS:leftfront_light_total:GAUGE:600:U:U',
         'DS:distance_min:GAUGE:600:U:U',
         'DS:distance_max:GAUGE:600:U:U',
         'DS:ticks_in_day:GAUGE:600:U:U',
         'DS:temperature_internal:GAUGE:600:U:U',
         'DS:temperature_external_1:GAUGE:600:U:U'        
        ]
        
        pendulum_source = [
        'DS:distance:GAUGE:1:U:U',
        'DS:intensity:GAUGE:1:U:U'
        ]
        
        data_sources=[
                'DS:speed1:COUNTER:600:U:U',
                'DS:speed2:COUNTER:600:U:U',
                'DS:speed3:COUNTER:600:U:U' ]
        
        date_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

        self.filename= f'pendulum.rrd'
        logging.info(f'---------------------------------------Loading RRD {self.filename}')
        while True:
            try:
                rrdtool.create(self.filename,
                         '--start', 'now-10s',
                         '--step', '1s',
                         '-O',
                         pendulum_source,
                         'RRA:MIN:0.5:1:1440',
                         'RRA:MAX:0.5:1:1440',
                         'RRA:AVERAGE:0.5:1:1440' )
                break
            except rrdtool.OperationalError as e:
                if e.args[0] == "creating 'pendulum.rrd': File exists":
                    os.rename('pendulum.rrd', f'pendulum_{date_str}.rdd')
                    logging.info(f'---------------------------------------RENAMED RRD {self.filename}')
                else:
                    logging.info(f'+++++   RRD Operational  Error....{e}   +++++')
            
        logging.info(f'---------------------------------------Loaded RRD {self.filename}')
                 
    def update(self, distance, intensity):
        try:
            rrdtool.update(self.filename, "N:%s:%s"% (distance, intensity))
        except rrdtool.OperationalError as e:
            logging.info(f'RRD File Error....{e}')
        
    def create_midifile(self):
        pass
        
    def update_rrdfile(self):
        pass

class MeasureThread(RGBEncoderThread):
    
    def __init__(self, thread_id, measure_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.measure_queue = measure_queue
        self.response_queue = response_queue
        self.done_event = done_event
        self.count = 0 
        self.do_measure = False
        self.setup()
        
        self.setup_watch()
        
    def setup_watch(self):
        self.laser_side = Laser(self.encoder, 1)
        self.laser_front = Laser(self.encoder, 2)
        
        self.laser_side.laser_on()
        time.sleep(2)
        self.laser_front.laser_on()
        time.sleep(2)
        self.laser_side.laser_off()
        time.sleep(2)
        self.laser_front.laser_off()
        
        logging.info('Building LDRS--------------------------------------------------------------------')
        
        atod = AtoD()
        
        self.LDR_front_left = LDR(self.laser_front, atod, 0)
        self.LDR_front_right = LDR(self.laser_front, atod, 1)
        self.LDR_left = LDR(self.laser_side, atod, 2)
        self.LDR_right = LDR(self.laser_side, atod, 3)
        
    def subscribe(self):
        while not self.done_event.is_set():
            try:
                #tick = self.measure_queue.get(timeout=2)
                tick = self.measure_queue.get_nowait()
                logging.info(f'got {tick}')
                if tick =="Run_Calibrate":
                    logging.info('Run_Calibrate Recieved...................................................................................')
                    self.do_measure = False 
                    self.run_calibrate()
                    self.do_measure = True 
                    
                if tick =="Start_Measure":
                    logging.info('Start Measuring Recieved...................................................................................')
                    self.do_measure = True 
            except Empty:
                #logging.info(f'Calibrate Thread {self.thread_id} did not receive a tick within timeout')
                # Aquire values...
                if self.do_measure: 
                    self.run_measure()
                    self.count = self.count + 1 
            
                # if self.count % 10 == 0:
                    # print(f' {self.count} measures')
                continue
                
        logging.info(f'Measure Thread {self.thread_id} SHUTTING DOWN....')
        self.shutdown()      
        
    def run_measure(self):
        # take_reading(self, times = 5, sleep_time = 0.001)
        
        self.LDR_front_left.take_reading()
        self.LDR_front_right.take_reading()
        
        #self.LDR_left.take_reading() 
        #self.LDR_right.take_reading() 
                
    def run_calibrate(self):
        print('YEAR:MH:DY:HR:MN:SC.MILLIS      d_av     l_av    diff  d_min  d_max thresd  l_min  l_max no sleep_time')
        # 2025:03:01:22:59:47.042525 FL (12008.4, 25123.8, 13115, 9636, 13162, 18566, 24601, 25973, 5, 0.1)
        for sleep_time in (0.1,0.05,0.01,0.005):
            logging.info(f'FL {self.LDR_front_left.take_reading(sleep_time = sleep_time)}')
            logging.info(f'FR {self.LDR_front_right.take_reading(sleep_time = sleep_time)}')
            logging.info(f'SL {self.LDR_left.take_reading(sleep_time = sleep_time)}')
            logging.info(f'SR {self.LDR_right.take_reading(sleep_time = sleep_time)}')  
        
        # self.watch_left_side.test(10)
        # self.watch_right_side.test(10)        
        # self.watch_front_right.test(10)
        # self.watch_front_left.test(10)
        
        try:
            pass
            # rrdtool.update(rrdfile, "N:%s:%s:%s:%s:%s"% (self.distance, a0,a1,a2,a3))
        except rrdtool.OperationalError:
            self.logger.info('Locked')
        
        logging.info(f'Calibaration completed---------------------------------------------------------------------------------------------')

class EncoderListenThread(RGBEncoderThread):
    
    def __init__(self, thread_id, tick_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.tick_queue = tick_queue
        self.response_queue = response_queue
        self.done_event = done_event
        self.setup()
        
        self.setup_listener()
        
    def setup_listener(self):
        logging.info('EncoderListening Thread Starting')
    
    def listen(self):
        while not self.done_event.is_set():
            if GPIO.input(self.INT_pin) == False: #
                self.Encoder_INT() 
                self.response_queue.put("Encoder Event")
                
            time.sleep(0.1)

        logging.info(f'Calibrate Thread {self.thread_id} SHUTTING DOWN....')
        self.shutdown()  

class RingBuffer:
    # # Example usage:
    # if __name__ == "__main__":
    # rb = RingBuffer(3)
            
    # rb.add(1)
    # print(f"After adding 1: max={rb.get_max()}, min={rb.get_min()}, avg={rb.get_avg()}")
    # print(f"Buffer: {rb.get_buffer()}")
    
    # rb.add(2)
    # print(f"After adding 2: max={rb.get_max()}, min={rb.get_min()}, avg={rb.get_avg()}")
    # print(f"Buffer: {rb.get_buffer()}")
    
    # rb.add(3)
    # print(f"After adding 3: max={rb.get_max()}, min={rb.get_min()}, avg={rb.get_avg()}")
    # print(f"Buffer: {rb.get_buffer()}")
    
    # rb.add(4)
    # print(f"After adding 4: max={rb.get_max()}, min={rb.get_min()}, avg={rb.get_avg()}")
    # print(f"Buffer: {rb.get_buffer()}")
    
    def __init__(self, size):
        """Initialize ring buffer with given size"""
        if size <= 0:
            raise ValueError("Size must be positive")
        
        self.size = size
        self.buffer = [0] * size  # Pre-allocate buffer with zeros
        self.pos = 0              # Current position for next write
        self.full = False         # Track if buffer is full
        self.count = 0            # Number of elements added
        
        # Statistics
        self.max_val = float('-inf')
        self.min_val = float('inf')
        self.sum = 0
        self.avg = 0.0
    
    def add(self, value):
        """Add a new integer value to the ring buffer"""
        if not isinstance(value, int):
            raise TypeError("Only integers are allowed")
            
        # Remove old value's contribution to statistics if buffer is full
        if self.full:
            old_value = self.buffer[self.pos]
            self.sum -= old_value
            
            # Update min/max if we're removing the current min/max
            if old_value == self.min_val or old_value == self.max_val:
                self._recalculate_min_max()
            else:
                self.max_val = max(self.max_val, value)
                self.min_val = min(self.min_val, value)
        else:
            self.max_val = max(self.max_val, value)
            self.min_val = min(self.min_val, value)
            self.count += 1
            if self.count == self.size:
                self.full = True
                
        # Add new value
        self.buffer[self.pos] = value
        self.sum += value
        self.avg = self.sum / self.count if self.count < self.size else self.sum / self.size
        
        # Update position
        self.pos = (self.pos + 1) % self.size
    
    def _recalculate_min_max(self):
        """Recalculate min and max when removing the current min/max"""
        if not self.buffer:
            self.max_val = float('-inf')
            self.min_val = float('inf')
            return
            
        self.max_val = max(self.buffer)
        self.min_val = min(self.buffer)
    
    def get_max(self):
        """Return current maximum value"""
        return self.max_val if self.count > 0 else None
    
    def get_min(self):
        """Return current minimum value"""
        return self.min_val if self.count > 0 else None
    
    def get_avg(self):
        """Return current average value"""
        return self.avg if self.count > 0 else None
    
    def get_buffer(self):
        """Return current buffer contents"""
        if not self.count:
            return []
        if self.full:
            return self.buffer[self.pos:] + self.buffer[:self.pos]
        return self.buffer[:self.pos]
    
    def is_full(self):
        """Return True if buffer is full"""
        return self.full
    
    def is_empty(self):
        """Return True if buffer is empty"""
        return self.count == 0

class DistanceThread(RGBEncoderThread):
    def __init__(self, thread_id, trigger_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.trigger_queue = trigger_queue
        self.response_queue = response_queue
        self.done_event = done_event
        
        self.setup()
        self.distance_setup()
        
        
    def distance_setup(self):
        self.values = []
        self.distance = 0 
                # Set up the distance sensor 
        self.tof = VL53L0X.VL53L0X(i2c_bus=1,i2c_address=0x29)
        self.tof.open()
        # Start ranging
        self.tof.start_ranging(VL53L0X.Vl53l0xAccuracyMode.BETTER)
    
        timing = self.tof.get_timing()
    
        if timing < 20000:
            timing = 20000
        logging.info("Timing %d ms" % (timing/1000))
        
    def run(self):
        sleep_time = 0.01
        while not self.done_event.is_set():
            try:
                tick = self.trigger_queue.get(timeout=2)
                if tick =='Distance_request':
                    value = self.read()
                    self.response_queue.put(f"Distance {value}")
                    self.encoder.writeLEDG(map_range(value, 10, 500, 0, 100))
                
                time.sleep(sleep_time)
            except Empty:
                continue
                
            time.sleep(sleep_time)

    def read(self):
        value = self.tof.get_distance()
        if value != 8190 and value != 0:
            self.distance = value
        return self.distance
 

def main():
    pendulum_rrd = Rrd('pendulum')
    last_distance = 0 
    global distance_logging
    
    # Setup communication queues
    logging.info('---------------------P Y T H O N     S T A R T ------------------------------------------------------------')
    tick_queue = Queue()
    measure_queue = Queue()
    trigger_queue = Queue()
    response_queue = Queue()
    encoder_queue = Queue()
    
    # Event to signal threads to stop
    done_event = threading.Event()
    close_logging = threading.Event()

    # Create instances
    tick_gen = TickGenerator(tick_queue, trigger_queue, done_event, tick_delay = tick_delay)
    RGBEncoder_helper = RGBEncoderThread(1, tick_queue, response_queue, done_event)
    RGBEncoder_listener = EncoderListenThread(3, tick_queue, encoder_queue, done_event)
    Measure_helper = MeasureThread(2, measure_queue, response_queue, done_event)
    Distance_helper = DistanceThread(4, trigger_queue, response_queue, done_event)

    # Create threads
    tick_generator = threading.Thread(target=tick_gen.run, name="TickGenerator")
    rgb_encoder_consumer = threading.Thread(target=RGBEncoder_helper.run, name=f"RGBEncoderThread-{1}")
    rgb_encoder_listener = threading.Thread(target=RGBEncoder_listener.listen, name=f"RGBEncoder-listener Thread-{2}")
    calibration_runner = threading.Thread(target=Measure_helper.subscribe, name=f"MeasureThread-{1}")
    log_thread = threading.Thread(target=log_consumer, args=(close_logging,), name="LogConsumer")
    distance_generator = threading.Thread(target=Distance_helper.run, name="Distance_generator")

    # Start threads
    tick_generator.start()
    rgb_encoder_consumer.start()
    rgb_encoder_listener.start()
    calibration_runner.start()
    distance_generator.start()
    log_thread.start()
    
    response_queue.put('Start_Measure')

    try:
        while True:
            # Check responses from helper threads
            try:
                response = response_queue.get_nowait()  # No_wait for measure loop
                if response == 'Start_Calibrate':
                    measure_queue.put('Run_Calibrate')
                    
                elif response == 'Start_Measure':
                    #logging.info(f' START MEASURE Response 1 {response}') 
                    measure_queue.put('Start_Measure')
                    
                elif response.find('Distance') == 0 :
                    distance = int(response[9:])
                    if distance_logging:
                        str = f'{datetime.datetime.now().strftime("%Y:%m:%d:%H:%M:%S.%f")} '
                        space_str =  ' ' * (int(distance / 4)) 
                        str = f'{datetime.datetime.now().strftime("%Y:%m:%d:%H:%M:%S.%f")} {space_str} * {distance}'
                         # { ' ' * (int(distance / 4)) } *  {distance }''
                        logging.info(str)
                    if distance == 8191:
                        distance = last_distance
                    pendulum_rrd.update(distance, 88)
                    last_distance = distance
                else:
                    logging.info(f'Response  {response}')
                    
            except Empty:
                pass
                
            try:
                encoder_event = encoder_queue.get_nowait()
                logging.info(f'RECIEVED Encoder Event {encoder_event}')
                if "Encoder Double Pushed" in encoder_event:
                    print('About to run calbrate after encoder double push')
                    measure_queue.put('Run_Calibrate')
                    print('Have sent run calbrate after encoder double push')
                    
                elif "Encoder Pushed" in encoder_event:
                    print('About to toggle distance display after encoder push')
                    distance_logging = not distance_logging
                    print('toggled distance display after encoder push')
                    
            except Empty:
                pass
            
            # logging.info(f'Response Q1: {response_queue.qsize()} Response Q2: {response_queue2.qsize()}   Measure Q: {measure_queue.qsize()}')
            time.sleep(0.01)  # Small delay to reduce CPU usage
    
    except KeyboardInterrupt:
        logging.info("Stopping threads...")
        done_event.set()
        tick_generator.join()
        rgb_encoder_consumer.join()
        rgb_encoder_listener.join()
        calibration_runner.join()
        distance_generator.join()
        
        close_logging.set()
        log_thread.join()  # Wait for the log thread to finish
        logging.info("All threads have been stopped")

if __name__ == "__main__":
    main()
