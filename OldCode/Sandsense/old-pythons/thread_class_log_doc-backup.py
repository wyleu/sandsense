import threading
import time
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

def map_range(x, in_min, in_max, out_min, out_max):
  return (x - in_min) * (out_max - out_min) // (in_max - in_min) + out_min

# User Questions:
# - Please write a python programme with three threads, one which generates a one second tick and two helper threads that acknowledge the tick, perform an action and then respond over two separate bi directional queues
# - The name of Empty is incorrect, you should import Empty from queue and use this in the except clauses
# - Thread 2 only appears to run once
# - Could you make the worker functions class based?
# - Could you add logging to standard out?
# - Could you add a timestamp to the logs?
# - Could logging be handed to a new separate thread?

# Configure logging to use a queue for asynchronous logging


logging_queue = Queue(-1)  # No limit on queue size
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(message)s', 
                    datefmt='%Y-%m-%d %H:%M:%S')

# Custom handler for logging to queue

class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            logging_queue.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)

# Remove all existing handlers and add our custom handler
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(QueueHandler())

def log_consumer(close_logging):
    """
    Consumes log messages from the queue and prints them to stdout.
    This function runs in its own thread to handle logging asynchronously.
    """
    while not close_logging.is_set():
        try:
            record = logging_queue.get(timeout=0.1)
            print(record)
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
        
        self.encoder.writeGP3conf(i2cEncoderLibV2.GP_PWM|
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
        #self.encoder.writeGP3(self.laser_brightish) # Lasers on   # GP3 tidied up with RGB LED
    
        logging.info('Laser1 %s'% (self.encoder.readGP1(),))
        logging.info('Laser2 %s' % (self.encoder.readGP2(),))
    
        logging.info('Laser1 conf %s' % (self.encoder.readGP1conf(),))
        logging.info('Laser2 conf %s' % (self.encoder.readGP2conf(),))
        
        self.encoder.writeGP1(255) # Laser off   255 off 0 full brightness
        self.encoder.writeGP2(255) # Lasers off
        
    def run(self):
        """
        Listens for ticks from the tick_queue, performs an action, and responds via response_queue.
        """
        
        while not self.done_event.is_set():
            try:
                tick = self.tick_queue.get(timeout=2)
                self.tick_LED = not self.tick_LED
                self.tick_got_count = self.tick_got_count + 1

                logging.info(f'Received tick: {tick}---count={self.tick_got_count}--run method')
                    
                if tick % 48 == 0 and self.tick_LED:          # Clock Minute
                    # logging.info('clock minute tick')
                    if tick % (48 * 60) == 0:       # Clock Hour
                        logging.info('clock Hour %s %s        WHITE' %(tick, time.ctime(time.time())))
                        self.encoder.writeLEDR(100)       # WHITE      HOUR
                        self.encoder.writeLEDB(100)
                        self.encoder.writeLEDG(100)
                        
                    elif tick % (48 * (60 + 15))== 0: # Clock Quarter
                        logging.info('clock Quarter %s %s     MAGENTA' %(tick, time.ctime(time.time())))
                        self.encoder.writeLEDR(100)       # MAGENTA      QUARTER
                        self.encoder.writeLEDB(100)
                        self.encoder.writeLEDG(0)
                    elif tick % (48 * (60 + 30))== 0: # Clock Half
                        logging.info('clock Half %s %s       GREEEN' %(tick, time.ctime(time.time())))
                        self.encoder.writeLEDR(0)           # GREEN      QUARTER
                        self.encoder.writeLEDB(0)
                        self.encoder.writeLEDG(100)                        
                    elif tick % (48 * (60 + 45))== 0: # Clock Three Quarter
                        logging.info('clock Three Quarter %s %s' %(tick, time.ctime(time.time())))
                        self.encoder.writeLEDR(0)       # CYAN      HOUR
                        self.encoder.writeLEDB(100)
                        self.encoder.writeLEDG(100)                        
                    else:                                 # Clock Hour
                        logging.info('clock Minute %s %s                      RED' %(tick, time.ctime(time.time())))
                        self.encoder.writeLEDR(100)       # Red      CLOCK MINUTE
                        self.encoder.writeLEDB(0)
                        self.encoder.writeLEDG(0)
                        self.response_queue.put("Start Calibrate")
                else:
                    if not self.tick_LED:
                        self.encoder.writeRGBCode(0x000000)
                    else:
                        self.encoder.writeRGBCode(0x000064)
                        
                        
                # time.sleep(0.5 + self.thread_id * 0.1)
                logging.info(f'Completed action for tick: {tick}')
                
                # Send response
                self.response_queue.put(f'Thread {self.thread_id} completed action')
            except Empty:
                logging.info(f'Thread {self.thread_id} did not receive a tick within timeout')
                continue
                
        self.shutdown()
       
    def shutdown(self):
       logging.info('Encoder Shutdown starting...')
       self.encoder.writeRGBCode(0x000000)
       self.encoder.writeGP1(self.laser_off) # Laser off
       self.encoder.writeGP2(self.laser_off) # Lasers off
       self.encoder.writeGP3(self.laser_off) # Lasers off
       logging.info('Encoder Shutdown cleanly...')
        
    def EncoderChange(self):
        self.encoder.writeLEDG(100)
        print ('Changed: %d' % (self.encoder.readCounter32()))
        self.encoder.writeLEDG(0)
    
    def EncoderPush(self):
        self.encoder.writeLEDB(100)
        print ('Encoder Pushed!')
        self.encoder.writeLEDB(0)
    
    def EncoderDoublePush(self):
        self.encoder.writeLEDB(100)
        self.encoder.writeLEDG(100)
        print ('Encoder Double Push!')
        self.encoder.writeLEDB(0)
        self.encoder.writeLEDG(0)
    
    def EncoderMax(self):
        self.encoder.writeLEDR(100)
        print ('Encoder max!')
        self.encoder.writeLEDR(0)
    
    def EncoderMin(self):
        self.encoder.writeLEDR(100)
        print ('Encoder min!')
        self.encoder.writeLEDR(0)
    
    def Encoder_INT(self):
        self.encoder.updateStatus()


class Viewer():
    def __init__(self):
        self.setup()
        
    def setup(self):
        # Set up the AtoD board.
        self.ADS = ADS1x15.ADS1115(1)
        logging.info("ADS:- %s" % (self.ADS,))
        self.ADS.setGain(self.ADS.PGA_4_096V)
        logging.info("ADS getMaxVolts:- %s" % (self.ADS.getMaxVoltage(),))
        logging.info("ADS getGain:- %s" % (self.ADS.getGain(),))
        logging.info("ADS.ADC0:- %s" % (self.ADS.readADC(0),))
        logging.info("ADS.ADC1:-%s" % (self.ADS.readADC(1),))
        logging.info("ADS.ADC2:-%s" % (self.ADS.readADC(2),))
        logging.info("ADS.ADC3:-%s" % (self.ADS.readADC(3),))
        logging.info("ADS getMaxVolts:-%s" % (self.ADS.getMaxVoltage(),))
        logging.info("ADS getGain:-%s" % (self.ADS.getGain(),))
        
        
    def read_all(self):
        self.a0 = self.ADS.readADC(0)
        self.a1 = self.ADS.readADC(1)
        self.a2 = self.ADS.readADC(2)
        self.a3 = self.ADS.readADC(3)

        self.average = ( self.a0 + self.a1 + self.a2 + self.a3 ) / 4

        
    def get_average(self):
        return self.average
        
    def display(self):
        logging.info("%s  %s  %s  %s  %s  %s" % (a0, a1, a2, a3,  self.distance , map_range(self.distance, 0, 1290, 0, 64)))

class LDR(Viewer):
    def __init__(self, sensor = 0 ):
        super().__init__()
        self.sensor = sensor
        
    def read(self):
        self.read_all()
        if self.sensor == 0:
            return self.a0
        if self.sensor == 1:
            return self.a1
        if self.sensor == 2:
            return self.a2
        if self.sensor == 3:
            return self.a3

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
    
    
class Watch():
    def __init__(self, name, laser, ldr, ldr2 = None):
        self.name = name
        self.laser = laser
        self.ldr = ldr
        self.ldr2 = ldr2
        
    def laser_on(self, brightness = 240):
        if self.laser.number == 1:
            self.laser.laser_on(brightness)
        if self.laser.number == 2:
            self.laser.laser_on(brightness)
        
    def laser_off(self):
        if self.laser.number == 1:
            self.laser.laser_off()
        if self.laser.number == 2:
            self.laser.laser_off()
            
    def all_laser_off(self):
        self.laser.all_laser_off()

        
    def read(self):
        return self.ldr.read()
        
    def test(self, times = 10):
        sleep_time_list = [0.1, 0.05, 0.01, 0.005, 0.001]
        
        for sleep_time in sleep_time_list: 
            dark_reading = 0 
            light_reading = 0 
            dark_total = 0
            light_total = 0 
            dark_max = 0 
            dark_min = 1000000
            light_max = 0
            light_min = 1000000 

            for i in range(times):
                if i == 1:
                    logging.info('%s TESTS OF LENGTH %s Seconds on %s: DARK=%s, LIGHT=%s, DIFFERENCE = %s' % (times, sleep_time, self.name, dark_reading, light_reading, light_reading - dark_reading))
                self.laser_off()
                time.sleep(sleep_time)
                dark_reading = self.read()
                dark_total = dark_total + dark_reading
                if dark_min > dark_reading:
                    dark_min = dark_reading
                if dark_max < dark_reading:
                    dark_max = dark_reading
                self.laser_on(240)
                time.sleep(sleep_time)
                light_reading = self.read()
                light_total = light_total + light_reading
                if light_min > light_reading:
                    light_min = light_reading
                if light_max < light_reading:
                    light_max = light_reading
                time.sleep(sleep_time)
                self.laser_off()
                
            dark_total = dark_total / times
            light_total = light_total / times
                
            logging.info('-AVG-%s %s, %s, -- %s' % (self.name, dark_total, light_total, light_total - dark_total))
            logging.info('-MAX-%s %s, %s, -- %s' % (self.name, dark_max, light_max, light_max - dark_min ))
            logging.info('-MIN-%s %s, %s, -- %s' % (self.name, dark_min, light_min, dark_max - light_min))
        
    def ident(self, sleep_time = 10):
        logging.info('IDENTING WATCH %s on Laser %s & LDR %s' % (self.name, self.laser.number, self.ldr.sensor))
        logging.info('TURNING OFF LASERS')
        self.all_laser_off()
        time.sleep(sleep_time)
        logging.info('TURNING ON %s LASER' % (self.name,))
        self.laser_on()
        time.sleep(sleep_time)
        logging.info('TURNING OFF LASERS')
        self.all_laser_off()
        time.sleep(sleep_time)
        logging.info('RUNNING TEST %s' % (self.name,))
        self.test()
        time.sleep(1)

class CalibrateThread(RGBEncoderThread):
    
    def __init__(self, thread_id, tick_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.tick_queue = tick_queue
        self.response_queue = response_queue
        self.done_event = done_event
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
        
        self.LDR_front_left = LDR(0)
        self.LDR_front_right = LDR(1)
        self.LDR_left = LDR(2)
        self.LDR_right = LDR(3)
        
        logging.info('Building WATCHES--------------------------------------------------------------------')
        
        self.watch_left_side = Watch('LEFT SIDE', self.laser_side, self.LDR_left)
        self.watch_right_side = Watch('RIGHT SIDE', self.laser_side, self.LDR_right)
        self.watch_front_right = Watch('WATCH FRONT RIGHT', self.laser_front, self.LDR_front_right)
        self.watch_front_left = Watch('WATCH FRONT LEFT', self.laser_front, self.LDR_front_left)
        
        logging.info('Starting to ident--------------------------------------------------------------------')
        
        
    
    def calibrate(self):
        while not self.done_event.is_set():
            try:
                tick = self.tick_queue.get(timeout=2)
                if tick =="Run Calibrate":
                    logging.info('Run Calibrate Recieved...................................................................................') 
                    self.run_calibrate()
                
                logging.info(f'Received tick: {tick}---{self.tick_got_count}')
                
                # time.sleep(0.5 + self.thread_id * 0.1)
                logging.info(f'Completed action for tick: {tick}')
                
                # Send response
                self.response_queue.put(f'Thread {self.thread_id} completed action')
            except Empty:
                logging.info(f'Calibrate Thread {self.thread_id} did not receive a tick within timeout')
                continue
                
        self.shutdown()                
                
    def run_calibrate(self):
        self.watch_left_side.ident(2)
        self.watch_right_side.ident(2)        
        self.watch_front_right.ident(2)
        self.watch_front_left.ident(2)

if __name__ == "__main__":
    # Setup communication queues
    tick_queue = Queue()
    calibrate_queue = Queue()
    response_queue1 = Queue()
    response_queue2 = Queue()
    
    # Event to signal threads to stop
    done_event = threading.Event()
    close_logging = threading.Event()

    # Create instances
    tick_gen = TickGenerator(tick_queue, done_event)
    RGBEncoder_helper = RGBEncoderThread(1, tick_queue, response_queue1, done_event)
    Calibrate_helper = CalibrateThread(2, calibrate_queue, response_queue2, done_event)

    # Create threads
    main_thread = threading.Thread(target=tick_gen.run, name="TickGenerator")
    helper_thread1 = threading.Thread(target=RGBEncoder_helper.run, name=f"RGBEncoderThread-{1}")
    helper_thread2 = threading.Thread(target=Calibrate_helper.calibrate, name=f"CalibrateThread-{2}")
    log_thread = threading.Thread(target=log_consumer, args=(close_logging,), name="LogConsumer")

    # Start threads
    main_thread.start()
    helper_thread1.start()
    helper_thread2.start()
    log_thread.start()

    try:
        while True:
            # Check responses from helper threads
            try:
                response1 = response_queue1.get_nowait()
                if response1 == 'Start Calibrate':
                    calibrate_queue.put('Run Calibrate')
                
                logging.info(response1)
            except Empty:
                pass

            try:
                response2 = response_queue2.get_nowait()
                logging.info(response2)
            except Empty:
                pass
            
            time.sleep(0.1)  # Small delay to reduce CPU usage
    
    except KeyboardInterrupt:
        logging.info("Stopping threads...")
        done_event.set()
        main_thread.join()
        helper_thread1.join()
        helper_thread2.join()
        
        close_logging.set()
        log_thread.join()  # Wait for the log thread to finish
        logging.info("All threads have been stopped")
