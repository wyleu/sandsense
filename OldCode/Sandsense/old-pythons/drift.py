import random
import smbus2
import RPi.GPIO as GPIO
import time
import os
import sys
import logging

import i2cEncoderLibV2
import os
from pathlib import Path
import rrdtool

import ADS1x15

import VL53L0X

import mido   # pip install python-rtmidi

rrdfile = "rrdfile-5.rrd"

def map_range(x, in_min, in_max, out_min, out_max):
  return (x - in_min) * (out_max - out_min) // (in_max - in_min) + out_min

class Recorder():
    def __init__(self, rrdfile):
                # Set up rrd database
    
        my_file = Path(rrdfile)
        if not my_file.is_file():
            rrdtool.create(
               rrdfile,
               "--start","now",
               "--step", "3600",
               "DS:Light_LEVEL_MAX:GAUGE:300:100:1000",
               "DS:LD1:GAUGE:10:0:30000",
               "DS:LD2:GAUGE:10:0:30000",
               "DS:LD3:GAUGE:10:0:30000",
               "DS:LD4:GAUGE:10:0:30000",
               "RRA:AVERAGE:0.5:1:1200"
            )
    def update(self, values):
        self.values = values
        try:
            rrdtool.update(rrdfile, "N:%s:%s:%s:%s:%s"% (self.distance, a0,a1,a2,a3))
        except rrdtool.OperationalError:
            self.logger.info('Locked')
            
class Viewer():
    def __init__(self, logger):
        self.logger = logger
        self.setup()
        
    def setup(self):
        # Set up the AtoD board.
        self.ADS = ADS1x15.ADS1115(1)
        self.logger.info("ADS:- %s" % (self.ADS,))
        self.ADS.setGain(self.ADS.PGA_4_096V)
        self.logger.info("ADS getMaxVolts:- %s" % (self.ADS.getMaxVoltage(),))
        self.logger.info("ADS getGain:- %s" % (self.ADS.getGain(),))
        self.logger.info("ADS.ADC0:- %s" % (self.ADS.readADC(0),))
        self.logger.info("ADS.ADC1:-%s" % (self.ADS.readADC(1),))
        self.logger.info("ADS.ADC2:-%s" % (self.ADS.readADC(2),))
        self.logger.info("ADS.ADC3:-%s" % (self.ADS.readADC(3),))
        self.logger.info("ADS getMaxVolts:-%s" % (self.ADS.getMaxVoltage(),))
        self.logger.info("ADS getGain:-%s" % (self.ADS.getGain(),))
        
        
    def read_all(self):
        self.a0 = self.ADS.readADC(0)
        self.a1 = self.ADS.readADC(1)
        self.a2 = self.ADS.readADC(2)
        self.a3 = self.ADS.readADC(3)

        self.average = ( self.a0 + self.a1 + self.a2 + self.a3 ) / 4

        
    def get_average(self):
        return self.average
        
    def display(self):
        self.logger.info("%s  %s  %s  %s  %s  %s" % (a0, a1, a2, a3,  self.distance , map_range(self.distance, 0, 1290, 0, 64)))

class LDR(Viewer):
    def __init__(self, logger, sensor = 0 ):
        super().__init__(logger)
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
  
class RGBEncoder():
    def __init__(self, logger):
        self.logger = logger
        self.setup()
        
    def setup(self):
        GPIO.setmode(GPIO.BCM)
        bus = smbus2.SMBus(1)
        
                # Output Laser
        
        self.laser_bright = 0
        self.laser_off = 255
        self.laser_dim = 245
        self.laser_brightish = 245
        
        
    
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
    
        self.logger.info ('Board ID code: 0x%X' % (self.encoder.readIDCode()))
        self.logger.info ('Board Version: 0x%X' % (self.encoder.readVersion()))
    
        self.encoder.writeRGBCode(0x640000)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x006400)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x000064)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x00)
        
        self.encoder.writeGP1(self.laser_brightish) # Laser on   255 off 0 full brightness
        self.encoder.writeGP2(self.laser_brightish) # Lasers on
        self.encoder.writeGP3(self.laser_brightish) # Lasers on
    
        self.logger.info('Laser1 %s'% (self.encoder.readGP1(),))
        self.logger.info('Laser2 %s' % (self.encoder.readGP2(),))
        self.logger.info('Laser3 %s' % (self.encoder.readGP3(),))
    
        self.logger.info('Laser1 conf %s' % (self.encoder.readGP1conf(),))
        self.logger.info('Laser2 conf %s' % (self.encoder.readGP2conf(),))
        self.logger.info('Laser3 conf %s' % (self.encoder.readGP3conf(),))
        
        self.encoder.writeGP1(self.laser_brightish) # Laser on   255 off 0 full brightness
        self.encoder.writeGP2(self.laser_brightish) # Lasers on
        self.encoder.writeGP3(self.laser_brightish) # Lasers on
        
    def shutdown(self):
       self.logger.info('Encoder Shutdown starting...')
       time.sleep(0.3)
       self.encoder.writeRGBCode(0x000000)
       self.encoder.writeGP1(self.laser_off) # Laser off
       self.encoder.writeGP2(self.laser_off) # Lasers off
       self.encoder.writeGP3(self.laser_off) # Lasers off
       self.logger.info('Encoder Shutdown cleanly...')
        
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

class Laser():
    def __init__(self, logger, RGBEncoder, number):
        self.number = number
        self.RGBEncoder = RGBEncoder
        self.logger = logger
        self.setup()
        
    def setup(self):
        self.logger.info('Laser instance created')
        pass
    
class Watch():
    def __init__(self,logger,name, laser, ldr, ldr2 = None):
        self.logger = logger
        self.name = name
        self.laser = laser
        self.ldr = ldr
        self.ldr2 = ldr2
        
    def laser_on(self, brightness = 240):
        if self.laser.number == 1:
            self.laser.RGBEncoder.encoder.writeGP1(brightness)
        if self.laser.number == 2:
            self.laser.RGBEncoder.encoder.writeGP2(brightness)
        if self.laser.number == 3:
            self.laser.RGBEncoder.encoder.writeGP3(brightness)
        
    def laser_off(self):
        if self.laser.number == 1:
            self.laser.RGBEncoder.encoder.writeGP1(255)
        if self.laser.number == 2:
            self.laser.RGBEncoder.encoder.writeGP2(255)
        if self.laser.number == 3:
            self.laser.RGBEncoder.encoder.writeGP3(255)
            
    def all_laser_off(self):
        self.laser.RGBEncoder.encoder.writeGP1(255)
        self.laser.RGBEncoder.encoder.writeGP2(255)
        self.laser.RGBEncoder.encoder.writeGP3(255)
        
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
                    self.logger.info('%s TESTS OF LENGTH %s Seconds on %s: DARK=%s, LIGHT=%s, DIFFERENCE = %s' % (times, sleep_time, self.name, dark_reading, light_reading, light_reading - dark_reading))
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
                
            self.logger.info('-AVG-%s %s, %s, -- %s' % (self.name, dark_total, light_total, light_total - dark_total))
            self.logger.info('-MAX-%s %s, %s, -- %s' % (self.name, dark_max, light_max, light_max - dark_min ))
            self.logger.info('-MIN-%s %s, %s, -- %s' % (self.name, dark_min, light_min, dark_max - light_min))
        
    def ident(self, sleep_time = 10):
        self.logger.info('IDENTING WATCH %s on Laser %s & LDR %s' % (self.name, self.laser.number, self.ldr.sensor))
        self.logger.info('TURNING OFF LASERS')
        self.all_laser_off()
        time.sleep(sleep_time)
        self.logger.info('TURNING ON %s LASER' % (self.name,))
        self.laser_on()
        time.sleep(sleep_time)
        self.logger.info('TURNING OFF LASERS')
        self.all_laser_off()
        time.sleep(sleep_time)
        self.logger.info('RUNNING TEST %s' % (self.name,))
        self.test()
        time.sleep(1)

class MyService():
    FIFO = '/tmp/mydriftservice_pipe'
    tick_delay = 60.0 / 48  # seconds
    
    minute_detect = None
    hour_detect = None
    quarter_detect = None
    half_detect = None
    threequarter_detect = None 

    def __init__(self,tick_delay = 60.0 / 48, delay=5):
        #  69120 ticks per day 
        # 2880  ticks per hour
        
        self.logger = self._init_logger()
        self.delay = delay
        self.tick_delay = tick_delay
        self.loop_count = 0 
        if not os.path.exists(MyService.FIFO):
            os.mkfifo(MyService.FIFO)
        self.fifo = os.open(MyService.FIFO, os.O_RDWR | os.O_NONBLOCK)
        self.logger.info('MyService instance created')
        self.logger.info('Running Setup')
        self.logger.info('Delay:=%s freq=%s' % (self.tick_delay, 1.0 /self.tick_delay))
        
        self.setup()

    def _init_logger(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        stdout_handler = logging.StreamHandler()
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(logging.Formatter('%(levelname)8s | %(message)s'))
        logger.addHandler(stdout_handler)
        return logger
        
    def setup(self):
        self.logger.info('MyService setup starting')
        self.start_time = time.time()
        self.encoder = RGBEncoder(self.logger)
        self.tick_state = True
        
        laser_side = Laser(self.logger, self.encoder, 1)
        laser_front = Laser(self.logger, self.encoder, 2)

        LDR_front_left = LDR(self.logger, 0)
        LDR_front_right = LDR(self.logger, 1)
        LDR_left = LDR(self.logger, 2)
        LDR_right = LDR(self.logger, 3)
        
        self.watch_left_side = Watch(self.logger,'LEFT SIDE', laser_side, LDR_left)
        self.watch_right_side = Watch(self.logger,'RIGHT SIDE', laser_side, LDR_right)
        self.watch_front_right = Watch(self.logger,'WATCH FRONT RIGHT', laser_front, LDR_front_right)
        self.watch_front_left = Watch(self.logger,'WATCH FRONT LEFT', laser_front, LDR_front_left)
        
        self.watch_left_side.ident(2)
        self.watch_right_side.ident(2)        
        self.watch_front_right.ident(2)
        self.watch_front_left.ident(2)
        
    def loop(self):
        self.loop_count = self.loop_count + 1
            
        self.tick(self.loop_count)
        self.report()
        
    def tick(self, loop_count):
        # self.logger.info(' Loop_Count:%s Minute:%s type %s' % (loop_count, minute, type(minute))) 
        
        quarter = time.ctime(time.time())[14:16]
        minute = time.ctime(time.time())[17:19]
        hour = time.ctime(time.time())[11:13]
        
        self.tick_state = not self.tick_state
        
        encoder = self.encoder.encoder
        quarter = int(quarter)

        if minute == "00":
            # self.logger.info('Loop_Count:%s Quarter:%s , Minute:%s ' % (loop_count, quarter, minute)) 
            match quarter:
                case 0:
                    if hour == '00':                  # MIDNIGHT
                        self.logger.info('------MIDNIGHT=00 White %s %s' %(loop_count, time.ctime(time.time())))
                        self.watch_left_side.test()
                        
                    self.logger.info('------quarter=00 %s %s                      WHITE' %(loop_count, time.ctime(time.time())))
                    encoder.writeLEDR(100)     # White   HOUR
                    encoder.writeLEDB(100)
                    encoder.writeLEDG(100)     
                    self.watch_left_side.test()           
                
                case 15:
                    self.logger.info('-----quarter=15 %s %s                     MAGENTA' %(loop_count, time.ctime(time.time())))
                    encoder.writeLEDR(100)     # Magenta   QUARTER
                    encoder.writeLEDB(100)
                    encoder.writeLEDG(0)
                    self.watch_left_side.test()
                                     
                case 30:
                    self.logger.info('----quarter=30 %s %s                      GREEN' %(loop_count, time.ctime(time.time())))
                    encoder.writeLEDR(0)       # Green     HALF
                    encoder.writeLEDB(0)
                    encoder.writeLEDG(100)
                    self.watch_left_side.test()
                    
                case 45:
                    self.logger.info('---quarter=45 %s %s                        CYAN' %(loop_count, time.ctime(time.time())))
                    encoder.writeLEDR(0)       # Cyan      THREE QUARTER
                    encoder.writeLEDB(100)
                    encoder.writeLEDG(100)
                    self.watch_left_side.test()
                    
                case _:
                    self.logger.info('--Minute %s %s                           YELLOW' %(loop_count, time.ctime(time.time())))
                    encoder.writeLEDR(100)     # Yellow     Minute
                    encoder.writeLEDB(0)
                    encoder.writeLEDG(100)
        else:   # Not minute
            # self.logger.info('Not Minute Blue %s %s' %(loop_count, time.ctime(time.time())))
            encoder.writeLEDR(0)       # Blue       Second
            encoder.writeLEDB(100)
            encoder.writeLEDG(0)
            
        if loop_count % 48 == 0:                  # Clock Minute
            # self.logger.info('clock minute tick')
            if loop_count % (48 * 60) == 0:       # Clock Hour
                self.logger.info('clock Hour %s %s' %(loop_count, time.ctime(time.time())))
            elif loop_count % (48 * (60 + 15))== 0: # Clock Quarter
                self.logger.info('clock Quarter %s %s' %(loop_count, time.ctime(time.time())))
            elif loop_count % (48 * (60 + 30))== 0: # Clock Half
                self.logger.info('clock Half %s %s' %(loop_count, time.ctime(time.time())))
            elif loop_count % (48 * (60 + 45))== 0: # Clock Three Quarter
                self.logger.info('clock Three Quarter %s %s' %(loop_count, time.ctime(time.time())))
            else:                                 # Clock Hour
                self.logger.info('clock Minute %s %s                         RED' %(loop_count, time.ctime(time.time())))
                encoder.writeLEDR(100)       # Red      CLOCK MINUTE
                encoder.writeLEDB(0)
                encoder.writeLEDG(0)
        else:
            pass

        if not self.tick_state:
            encoder.writeLEDR(0)           # off
            encoder.writeLEDG(0)
            encoder.writeLEDB(0)
        
    def report(self):
        self.stop_time = time.time()
        self.duration = self.stop_time - self.start_time
        # self.logger.info('%s Ticks from %s to %s a time of %s: %s Ticks per minute: day(69120) %s' % (
               # self.loop_count,
               # time.ctime(self.start_time),
               # time.ctime(self.stop_time),
               # self.duration,
               # 60 * self.loop_count / self.duration,
               # 24 * 60 * 60 * self.loop_count / self.duration   )
               # )

    def shutdown(self):
       time.sleep(0.3)
       self.encoder.shutdown()
       self.report()
       self.logger.info('Shutdown cleanly...')

    def start(self):
        try:
            while True:
                self.loop()
                delay = self.tick_delay - (time.time() % self.tick_delay)
                # self.logger.info('sleep delay:-%s' % (delay,))
                time.sleep(delay)  # Correct for drift, ensuring exact timing
                
        except KeyboardInterrupt:
            self.logger.warning('Keyboard interrupt (SIGINT) received...')
            self.stop()
        self.stop()

    def stop(self):
        self.logger.info('Cleaning up...')
        self.shutdown()
        if os.path.exists(MyService.FIFO):
            os.close(self.fifo)
            os.remove(MyService.FIFO)
            self.logger.info('Named pipe removed')
        else:
            self.logger.error('Named pipe not found, nothing to clean up')
        sys.exit(0)
                
class Tick():
    def __init__(self):
        # Set up Midi port
        self.ioports = mido.get_ioport_names()
        self.outputs = mido.get_output_names()
    
    def status(self):
        print(self.ioports)
        print(self.outputs)
        
    def tick(self):
        pass

def worktick():
    # Simulate some processing time and activity
    processing_time = random.uniform(0.05, 0.2)  # Random delay between 0.05 and 0.2 seconds
    time.sleep(processing_time)
    
    # Random events that might occur with each tick
    events = {
        0: "System check passed",
        1: "Low memory warning",
        2: "CPU usage spike",
        3: "Network connection stable",
        4: "Data backup completed"
    }
    event = random.choice(list(events.keys()))
    
    # Log the tick with the event
    log_message = f"Tick event: {events[event]} (Processing time: {processing_time:.3f}s)"
    print(log_message)

def main():

    service = MyService()
    service.start()
    
if __name__ == "__main__":
    
    main()
