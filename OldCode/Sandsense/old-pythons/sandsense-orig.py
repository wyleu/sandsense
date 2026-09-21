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

rrdfile = "rrdfile-5.rrd"



def map_range(x, in_min, in_max, out_min, out_max):
  return (x - in_min) * (out_max - out_min) // (in_max - in_min) + out_min


class MyService:
    FIFO = '/tmp/myservice_pipe'

    def __init__(self, delay=5):
        self.logger = self._init_logger()
        self.delay = delay
        if not os.path.exists(MyService.FIFO):
            os.mkfifo(MyService.FIFO)
        self.fifo = os.open(MyService.FIFO, os.O_RDWR | os.O_NONBLOCK)
        self.logger.info('MyService instance created')
        self.logger.info('Running Setup')
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
    
        GPIO.setmode(GPIO.BCM)
        bus = smbus2.SMBus(1)
    
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
    
        self.logger.info ('Board ID code: 0x%X' % (self.encoder.readIDCode()))
        self.logger.info ('Board Version: 0x%X' % (self.encoder.readVersion()))
    
        self.encoder.writeRGBCode(0x640000)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x006400)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x000064)
        time.sleep(0.3)
        self.encoder.writeRGBCode(0x00)
    
    
        # Output Laser
        
        self.laser_bright = 0
        self.laser_off = 255
        self.laser_dim = 245
        self.laser_brightish = 245
        
        self.encoder.writeGP1(self.laser_brightish) # Laser on   255 off 0 full brightness
        self.encoder.writeGP2(self.laser_brightish) # Lasers on
    
        self.logger.info('Laser1 %s'% (self.encoder.readGP1(),))
        self.logger.info('Laser2 %s' % (self.encoder.readGP2(),))
    
        self.logger.info('Laser1 conf %s' % (self.encoder.readGP1conf(),))
        self.logger.info('Laser2 conf %s' % (self.encoder.readGP2conf(),))
    
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
    
        # Set up rrd database
    
        my_file = Path(rrdfile)
        if not my_file.is_file():
            rrdtool.create(
               rrdfile,
               "--start","now",
               "--step", "1",
               "DS:pendulum:GAUGE:300:100:1000",
               "DS:LD1:GAUGE:10:0:30000",
               "DS:LD2:GAUGE:10:0:30000",
               "DS:LD3:GAUGE:10:0:30000",
               "DS:LD4:GAUGE:10:0:30000",
               "RRA:AVERAGE:0.5:1:1200"
            )
    
        # Set up the distance sensor 
        self.tof = VL53L0X.VL53L0X(i2c_bus=1,i2c_address=0x29)
        self.tof.open()
        # Start ranging
        self.tof.start_ranging(VL53L0X.Vl53l0xAccuracyMode.BETTER)
    
        timing = self.tof.get_timing()
    
        if timing < 20000:
            timing = 20000
        self.logger.info("Timing %d ms" % (timing/1000))
    
    
        # activity toggle blue lED
    
        self.activity = True
        self.activity_count = 0
        self.activity_threshold =  20 
    
        # GPIO.add_event_detect(self.INT_pin, GPIO.FALLING, callback=Encoder_INT, bouncetime=10)
    
        self.encoder.writeRGBCode(0x006400)
        self.distance = 0
        self.distance_max = 0   # 1280 ++ 
        self.distance_min = 0   # 4 ++
        
        
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
        
        
    def loop(self):
        if GPIO.input(self.INT_pin) == False: #
            self.Encoder_INT() #
            self.encoder.writeLEDG(int(map_range(self.distance, 0, 1290, 0, 100)))

        value = self.tof.get_distance()
        if value != 8190 and value != 0:
            self.distance = value

        a0 = self.ADS.readADC(0)
        a1 = self.ADS.readADC(1)
        a2 = self.ADS.readADC(2)
        a3 = self.ADS.readADC(3)

        self.average = ( a0 + a1 + a2 + a3 ) / 4
        self.logger.info("%s  %s  %s  %s  %s  %s" % (a0, a1, a2, a3,  self.distance , map_range(self.distance, 0, 1290, 0, 64)))

        self.encoder.writeLEDG(int(map_range(self.distance, 0, 1290, 0, 100)))
        self.encoder.writeLEDR(int(map_range(self.average, 0, 32768, 0, 100)))     #32768

        try:
            rrdtool.update(rrdfile, "N:%s:%s:%s:%s:%s"% (self.distance, a0,a1,a2,a3))
        except rrdtool.OperationalError:
            self.logger.info('Locked')

        
        # Toggle actiity LED
        if self.activity_count == self.activity_threshold:
            self.activity_count = 0
            self.encoder.writeLEDB(100)
            self.encoder.writeGP1(self.laser_dim) # Laser on
            self.encoder.writeGP2(self.laser_off) # Laser on
        else:
            self.encoder.writeLEDB(0)
            self.encoder.writeGP1(self.laser_off) # Laser on
            self.encoder.writeGP2(self.laser_dim) # Laser on
        self.activity = not self.activity
        self.activity_count = self.activity_count + 1 
        
    def shutdown(self):
       time.sleep(0.3)
       self.encoder.writeRGBCode(0x000000)
       self.encoder.writeGP1(self.laser_off) # Laser off
       self.encoder.writeGP2(self.laser_off) # Lasers off
       self.logger.info('Shutdown cleanly...')

    def start(self):
        try:
            while True:
                self.loop()
        except KeyboardInterrupt:
            self.logger.warning('Keyboard interrupt (SIGINT) received...')
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


if __name__ == '__main__':
    service = MyService()
    service.start()
