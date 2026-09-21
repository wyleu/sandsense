"""
One Wire

/sys/bus/w1/device/

cat 10-00080135cc59/temperature; cat 10-00080135df86/temperature; cat 10-000801362fa3/temperature; cat 10-0008013625e8/temperature
"""

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
import ADS1x15
import VL53L0X
import mido   # pip install python-rtmidi

from queue import Queue, Empty, Full
from pathlib import Path

date_str = datetime.datetime.now().strftime("Logfile_%Y:%m:%d:%H:%M:%S")

distance_logging = False

midi_file_overwrite = False     # Regenerate the MIDI Files.

laser_intensity = 200
laser_bright = 0
laser_off = 255
laser_dim = 245
laser_brightish = 245

GPIO.setmode(GPIO.BCM)
bus = smbus2.SMBus(1)
INT_pin = 17

GPIO.setup(INT_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
"""https://raspberrypi.stackexchange.com/questions/147332/rpi-gpio-runtimeerror-failed-to-add-edge-detection

sudo apt remove python3-rpi.gpio
sudo apt update
sudo apt install python3-rpi-lgpio
No change in code 

import RPi.GPIO as GPIO

or so it says.
"""
encoder = i2cEncoderLibV2.i2cEncoderLibV2(bus, 0x47)

def EncoderChange():
	#encoder.writeLEDG(100)
	response_queue.put(f'Changed: {encoder.readCounter32()}')
	#encoder.writeLEDG(0)
def EncoderPush():
	#encoder.writeLEDB(100)
	response_queue.put(f' Encoder Pushed')
	#logging.info ('Encoder Pushed!')
	#encoder.writeLEDB(0)
def EncoderDoublePush():
	#encoder.writeLEDB(100)
	#encoder.writeLEDG(100)
	response_queue.put(f' Encoder Double Pushed')
	#logging.info ('Encoder Double Push!')
	#encoder.writeLEDB(0)
	#encoder.writeLEDG(0)
def EncoderMax():
	#encoder.writeLEDR(100)
	response_queue.put(f' Encoder Max')
	#logging.info ('Encoder max!')
	#encoder.writeLEDR(0)
def EncoderMin():
	#encoder.writeLEDR(100)
	response_queue.put(f' Encoder Min')
	#logging.info ('Encoder min!')
	#encoder.writeLEDR(0)
def Encoder_INT():
	encoder.updateStatus()

unix_clock_ticks_per_second = os.sysconf('SC_CLK_TCK')

beats_per_minute = 48   # 48   Sandbach church clock
beats_per_minute = 48 * 3/2  # 48   Duncan White clock
beats_per_hour = beats_per_minute * 60 
beats_per_day = beats_per_hour * 24

ticks_per_beat   = 2

ticks_per_minute = beats_per_minute * ticks_per_beat
ticks_per_hour = beats_per_hour * ticks_per_beat
ticks_per_day = beats_per_day * ticks_per_beat

tick_delay = 60.0 / ticks_per_minute

tick_queue_size = 50
tick_queue_size_warn = 50 * 3 / 4 

ADC_samples_per_second = 128 
ADC_sleep_time = 0.001
ADC_loop_delay = 1 / ADC_samples_per_second
ADC_light_level_average_count = int(10 / 2  * ADC_samples_per_second)     # average over 10 second
ADC_toggle_mode = False                                                   # False = Continous lasers     True Pulsed lasers

print(f'ADC_samples_per_second {ADC_samples_per_second}')
print(f'ADC_loop_delay {ADC_loop_delay}')
print(f'ADC_light_level_average_count {ADC_light_level_average_count}')
print(f'ADC_toggle_mode {ADC_toggle_mode}')

def map_range(x, in_min, in_max, out_min, out_max):
	try:
		return (x - in_min) * (out_max - out_min) // (in_max - in_min) + out_min
	except (ZeroDivisionError, TypeError):
		return int(out_max  - out_min / 2 ) + out_min
		

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

logfile = f'logs/{date_str}.log'

fh = logging.FileHandler(logfile)
fh.setLevel(logging.DEBUG)
root_logger.addHandler(fh)

logging.info(f'logging to {logfile}')

logging.debug('This message shouldnt go to the log file')
logging.info('This message should go to the log file')
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
	
class BeatGenerator:
	def __init__(self, beat_queue, midi_queue,  beat_control_queue, done_event):
		self.beat_queue = beat_queue
		self.beat_control_queue = beat_control_queue
		self.midi_queue = midi_queue
		self.done_event = done_event
		self.tick_count = 0 
		self.beat_count = 0
		self.adjust_count = 0 
		self.midi_on = False

	def run(self):
		delay = 0 
		"""
		Generates a tick every second and places it into the beat_queue.
		"""
		
		self.adjust_delay = self.tick_delay =  tick_delay
		last_adjust_time = time.time()
		
		
		
		logging.info(f'TICKS PER BEAT:-{ ticks_per_beat }')
		while not self.done_event.is_set():
			if self.midi_on:
				self.midi_queue.put({'type':'note_on', 'note':53 - 24  , 'velocity': 50})
				encoder.writeLEDR(100)
			else:
				self.midi_queue.put({'type':'note_off', 'note':53 - 24  , 'velocity': 50})
				encoder.writeLEDR(0)
			self.midi_on = not self.midi_on
			try:
				beat_alter = self.beat_control_queue.get(block = False, timeout=None)  # Don't block on adjust
				
				self.adjust_delay = time.time() - last_adjust_time
				last_adjust_time = time.time()
				
				self.adjust_count = self.adjust_count + 1
				if self.adjust_delay >  1.4 * self.tick_delay:
					pass
				elif abs(self.adjust_delay - self.tick_delay) > 0.00025:
				# self.adjust_delay = self.adjust_delay - (time.time()  % self.adjust_delay)
					self.tick_delay = self.tick_delay  +  ((self.adjust_delay - self.tick_delay) / 25) 
					logging.info(f'Tick Delay {self.tick_delay:.6f}  Adjust delay { self.adjust_delay:.6f} diff {self.tick_delay - self.adjust_delay:.8f}')
				
			except Empty:
				pass
				
			except Full:
				logging.info(f'Full Queue..........................')
				
			except Exception as e:
				logging.info(f'Exception: {e}')
				exc_type, exc_obj, exc_tb = sys.exc_info()
				fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
				print(exc_type, fname, exc_tb.tb_lineno)
				
				
			# if self.tick_count % ticks_per_beat == 0:
				# self.beat_queue.put(self.beat_count)
				
				# logging.info(f'self.beat_Count {self.beat_count}')
			self.beat_count = self.beat_count + 1
				
			delay = self.tick_delay - (time.time() % self.tick_delay)

			#logging.info('Generated a tick of %s secs with delay of %0.6f  Ticks: %s ' % (tick_delay, delay, self.tick_count))
			#logging.info('sleep delay:-%s' % (delay,))
				
			time.sleep(delay)  # Correct for drift, ensuring exact timing
			logging.info(f' {1 / self.tick_delay} Hz                  Tick Delay {self.tick_delay:.6f}  Adjust delay { self.adjust_delay:.6f} delay     {delay:.8f}      tick{self.tick_count} beat {self.beat_count} adjust {self.adjust_count}')
			self.tick_count = self.tick_count + 1
			
class CircularBuffer(object):
	def __init__(self, size, name = None):
		"""initialization"""
		self.index= 0
		self.size= int(size)
		self._data = []
		self.max = 0
		self.min = 32768
		self.max_overall = 0
		self.min_overall = 32767
		self.max = self.max_overall
		self.min = self.min_overall
		self.max_threshold = 32767
		self.min_threshold = 0 
		self.name= name
		self.reset_max_min()
		logging.info(f'Circular Buffer {self.name}  self.size {self.size}')

	def reset_max_min(self):
		if self.max > self.max_overall:
			self.max_overall = self.max
			self.max_threshold = int(self.max_overall  * 1.01)
		if self.min < self.min_overall:
			self.min_overall = self.min
			self.min_threshold = int(self.min_overall * 0.99 ) 
			
		
	def record(self, value):
		"""append an element"""
		if value > self.max:
			self.max = value
	
		if value < self.min:
			self.min = value
			
		# if len(self._data) == self.size:
			# self._data[self.index]= value
		# else:
			# self._data.append(value)
		# self.index= (self.index + 1) % self.size

	def __getitem__(self, key):
		"""get element by index like a regular array"""
		return(self._data[key])

	def __repr__(self):
		"""return string representation"""
		return self._data.__repr__() + ' (' + str(len(self._data))+' items)'

	def get_all(self):
		"""return a list of all the elements"""
		return(self._data)

	def get_average(self):
		try:
			return sum(self.get_all()) / len(self.get_all())
		except ZeroDivisionError:   # Cludge
			return 0 
			
	def get_high_threshold(self):
		return int(self.get_max() * 1.01)
		
	def get_low_threshold(self):
		return int(self.get_min() * 0.99)
		
	def get_mid_threshold(self):
		return int(self.get_min() + ((self.get_max() - self.get_min()) / 2 ))
		
	def get_max(self):
		return self.max
		
	def get_min(self):
		return self.min
		
	def display(self):
		return( f"""{self.name} {self.min_overall:.0f} < {self.get_low_threshold():.0f}<{ self.get_min():.0f}<>{self.get_mid_threshold():.0f}<>{self.get_max():.0f} {self.get_high_threshold():.0f} <{self.max_overall:.0f}""")
		

# q= CircularBuffer(100);
# for i in range(400):
	# q.record(i);
	
# print ("capacity=", q.size)
# print ("stored=", len(q.get_all()))
# print ("average=", (sum(q.get_all()) / len(q.get_all())))

# Results in:

# capacity= 1000000
# stored= 40000
# average= 19999

# real 0m0.024s
# user 0m0.020s
# sys  0m0.000s

class EncoderThread():
	def __init__(self,response_queue, done_event):
		self.chime_queue = chime_queue
		self.response_queue = response_queue
		self.done_event = done_event
		
	def run(self):
		while not self.done_event.is_set():

			if GPIO.input(INT_pin) == False: 
				Encoder_INT() 
			time.sleep(0.1)
			
		self.shutdown()
	   
	def shutdown(self):
	   logging.info('Encoder Shutdown starting...')
	   # encoder.writeRGBCode(0x000000)
	   # encoder.writeGP1(laser_off) # Laser off
	   # encoder.writeGP2(laser_off) # Lasers off
	   logging.info('Encoder Shutdown cleanly...')
		
class ChimeThread():
	
	def __init__(self, thread_id, chime_queue, response_queue, done_event):
		self.thread_id = thread_id
		self.chime_queue = chime_queue
		self.response_queue = response_queue
		self.done_event = done_event


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
		
	def shutdown(self):
		logging.info('Chime Thread Shutdown starting...')
		# encoder.writeRGBCode(0x000000)
		# encoder.writeGP1(laser_off) # Laser off
		# encoder.writeGP2(laser_off) # Lasers off
		logging.info('Chime Thread Shutdown cleanly...')
        
         
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
	def __init__(self, thread_id, midi_queue, chime_queue, response_queue, done_event):
		self.thread_id = thread_id
		self.midi_queue = midi_queue
		self.response_queue = response_queue
		self.chime_queue = chime_queue
		self.done_event = done_event

		self.setup()

		
	def setup(self):
		MIDI_PORT = 'QmidiNet'
		self.midi_got_count = 0 
		self.midi_on = True
		
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
  
		
	def run(self):
		"""
		Listens for ticks from the midi_queue, performs an action
		"""
		
		while not self.done_event.is_set():
			try:
				midi_result = self.midi_queue.get(timeout=2)
				self.midi_got_count = self.midi_got_count + 1
				self.midi_on = not self.midi_on
				# logging.info(f'.................................................Received tick: {midi_result}---count={self.midi_got_count}')

				self.port.send(mido.Message(type = midi_result['type'], note = midi_result['note'], velocity = midi_result['velocity']))
					 
				# logging.info(f'Completed action for beat: {beat}')
				
				# Send response
				# self.response_queue.put(f'Thread {self.thread_id} completed action')
			except Empty:
				# logging.info(f'Thread {self.thread_id} did not receive a beat within timeout')
				continue
			except Exception as e:
				logging.info(f'Exception: {e}')
				exc_type, exc_obj, exc_tb = sys.exc_info()
				fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
				print(exc_type, fname, exc_tb.tb_lineno)
				
		self.shutdown()
	   
	def shutdown(self):
	   logging.info('MIDI Shutdown starting...')
	   # encoder.writeRGBCode(0x000000)
	   # encoder.writeGP1(laser_off) # Laser off
	   # encoder.writeGP2(laser_off) # Lasers off
	   logging.info('MIDI Shutdown cleanly...')
	   
	   
class MonitorThread():
	def __init__(self, thread_id, beat_control_queue, midi_queue, response_queue, done_event):
		self.thread_id = thread_id
		self.beat_control_queue = beat_control_queue
		self.response_queue = response_queue
		self.done_event = done_event
		
		self.setup()
		
		#GPIO.setmode(GPIO.BCM)
		#bus = smbus2.SMBus(1)
		#self.INT_pin = 17
		
		self.laser_setup()
		self.atod_setup()
		
	def setup(self):
		self.beat_got_count = 0 
		self.beat_on = True
		laser_on = True
		self.average_light_level = 0 
		self.samples = 0 
		
		
	def laser_setup(self):
		#Encoder set up

		encconfig = (
			i2cEncoderLibV2.INT_DATA |
			i2cEncoderLibV2.WRAP_ENABLE |
			i2cEncoderLibV2.DIRE_RIGHT |
			i2cEncoderLibV2.IPUP_ENABLE |
			i2cEncoderLibV2.RMOD_X1 |
			i2cEncoderLibV2.RGB_ENCODER)
	
		encoder.begin(encconfig)
	
		encoder.writeCounter(0)
		encoder.writeMax(35)
		encoder.writeMin(00)
		encoder.writeStep(1)
		encoder.writeAntibouncingPeriod(8)
		encoder.writeDoublePushPeriod(50)
	
		encoder.writeGammaRLED(i2cEncoderLibV2.GAMMA_2)
		encoder.writeGammaGLED(i2cEncoderLibV2.GAMMA_2)
		encoder.writeGammaBLED(i2cEncoderLibV2.GAMMA_2)
	
		encoder.writeGP1conf(i2cEncoderLibV2.GP_PWM |
			i2cEncoderLibV2.GP_PULL_DI |
			i2cEncoderLibV2.GP_INT_DI
		)
	
		encoder.writeGP2conf(i2cEncoderLibV2.GP_PWM|
			 i2cEncoderLibV2.GP_PULL_DI |
			 i2cEncoderLibV2.GP_INT_DI
		)
		
		encoder.onChange = EncoderChange
		encoder.onButtonPush = EncoderPush
		encoder.onButtonDoublePush = EncoderDoublePush
		encoder.onMax = EncoderMax
		encoder.onMin = EncoderMin
	
		encoder.autoconfigInterrupt()
	
		logging.info ('Board ID code: 0x%X' % (encoder.readIDCode()))
		logging.info ('Board Version: 0x%X' % (encoder.readVersion()))
		
		encoder.writeRGBCode(0x640000)
		time.sleep(0.3)
		encoder.writeRGBCode(0x006400)
		time.sleep(0.3)
		encoder.writeRGBCode(0x000064)
		time.sleep(0.3)
		encoder.writeRGBCode(0x00)
		
		
		#  sudo required for following line.
		#  gpioinfo
		#  GPIO.add_event_detect(INT_pin, GPIO.FALLING, callback=Encoder_INT, bouncetime=10)
		
		encoder.writeGP1(laser_brightish) # Side Lasers on   255 off 0 full brightness
		encoder.writeGP2(laser_brightish) # Front Laser on
	
		logging.info('Laser1 %s'% (encoder.readGP1(),))
		logging.info('Laser2 %s' % (encoder.readGP2(),))
	
		logging.info('Laser1 conf %s' % (encoder.readGP1conf(),))
		logging.info('Laser2 conf %s' % (encoder.readGP2conf(),))
		
		encoder.writeGP1(255) # Side Lasers off   255 off 0 full brightness
		encoder.writeGP2(255) # Front Laser off
		
	def atod_setup(self):

		self.bright_a0 = CircularBuffer(ADC_light_level_average_count,'bright A0')
		self.bright_a1 = CircularBuffer(ADC_light_level_average_count, 'bright A1')
		if ADC_toggle_mode:
			self.dull_a0 = CircularBuffer(ADC_light_level_average_count,' dull  A0')
			self.dull_a1 = CircularBuffer(ADC_light_level_average_count, ' dull  A1')

		# Manage the LDR's
		logging.info('...............................................................Starting up AtoD')
		# Set up the AtoD board.
		self.ADS = ADS1x15.ADS1115(1,0x4a)
		logging.info("ADS:- %s" % (self.ADS,))
		self.ADS.setGain(self.ADS.PGA_4_096V)
		self.display()

	def display(self):
		""" These bits control the data rate setting.
					000b : 8SPS
					001b : 16SPS
					010b : 32SPS
					011b : 64SPS
					100b : 128SPS (default)
					101b : 250SPS
					110b : 475SPS
					111b : 860SPS
		"""
		data_rates = { 
			0 : '8 SPS',
			1 : '16 SPS',
			2 : '32 SPS',
			3 : '64  SPS',
			4 : '128 SPS Default',
			5 : '250 SPS',
			6 : '475 SPS',
			7 : '860 SPS',
		}
		
		logging.info("ADS getMaxVolts:- %s" % (self.ADS.getMaxVoltage(),))
		logging.info("ADS getGain:- %s" % (self.ADS.getGain(),))
		logging.info("ADS.ADC0:- %s" % (self.ADS.readADC(0),))
		logging.info("ADS.ADC1:-%s" % (self.ADS.readADC(1),))
		logging.info("ADS.ADC2:-%s" % (self.ADS.readADC(2),))
		logging.info("ADS.ADC3:-%s" % (self.ADS.readADC(3),))
		logging.info("ADS.getDataRate:-%s" % (data_rates[self.ADS.getDataRate()],))
		
		logging.info("ADS getMaxVolts:-%s" % (self.ADS.getMaxVoltage(),))
		logging.info("ADS getGain:-%s" % (self.ADS.getGain(),))
	
	def shutdown(self):
	   logging.info('Encoder Shutdown starting...')
	   encoder.writeRGBCode(0x000000)
	   encoder.writeGP1(laser_off) # Side Lasers off
	   encoder.writeGP2(laser_off) # Front Lasers off
	   logging.info('Encoder Shutdown cleanly...')  
		
	def run(self):
		note_on = False
		note_one_count = 0
		note_two_count = 0
		last_time = time.time()
		last_sample = 0
		
		while not self.done_event.is_set():
			try:
				#tick = self.beat_control_queue.get(timeout=2)
				# write lasers and read ldrs 
				
				#if self.beat_control_queue.qsize() > tick_queue_size_warn:
				#    logging.info(f'------------------------------------------queue length:{self.beat_control_queue.qsize()} tick {tick} a0 {self.a0}, a 1 {self.a1} Laser { laser_on}    QUEUE FILING!!!!') # , self.a2, self.a3)
				for av in range(int(ADC_light_level_average_count)):
					#for i in range(2):     # Laser on/off
					if True:
						t = time.time()
						# self.ADS.requestADC(0)
						# self.a0 = self.ADS.getValue()    # front
						# self.ADS.requestADC(1)
						# self.a1 = self.ADS.getValue()    # front
						
						
						self.samples = self.samples + 1
						self.a0 = self.ADS.readADC(0)    # side
						self.a1 = self.ADS.readADC(1)    # side
						if ADC_toggle_mode:
							self.m0 = map_range(self.a0, self.dull_a0.min_threshold, self.bright_a0.max_threshold, 0, 127)
							self.m1 = map_range(self.a1, self.dull_a1.min_threshold, self.bright_a1.max_threshold, 0, 127)
						
							#self.m2 = map_range(self.a2, 1, 32768, 1, 127)
							#self.m3 = map_range(self.a3, 1, 32768, 1, 127)
							#self.port.send(mido.Message(type='note_on'))
							#self.port.send(mido.Message(type='note_off'))
							#self.port.send(mido.Message(type='control_change',control = 100 + i, value = self.m2))
							#self.port.send(mido.Message(type='control_change',control = 100 + i, value = self.m3))
						
						
							if laser_on: 
								self.bright_a0.record(self.a0)
								self.bright_a1.record(self.a1)
								encoder.writeGP1(255) # Side Lasers off   255 off 0 full brightness
								encoder.writeGP2(255) # Front Laser off
							else:
								self.dull_a0.record(self.a0)
								self.dull_a1.record(self.a1)
								encoder.writeGP1(laser_intensity) # Side Laser off   255 off 0 full brightness
								encoder.writeGP2(laser_intensity) # Front Laser off
							laser_on = not laser_on
						else:
							self.bright_a0.record(self.a0)
							self.bright_a1.record(self.a1)
							encoder.writeGP1(255) # Side Laser off   255 off 0 full brightness
							encoder.writeGP2(laser_intensity) # Front Laser 
							
						if self.a0 <= self.bright_a0.get_mid_threshold() and note_on:
							self.m0 = map_range(self.a0, self.bright_a0.get_low_threshold(), self.bright_a0.get_high_threshold(), 0, 127)
							self.m1 = map_range(self.a1, self.bright_a1.get_low_threshold(), self.bright_a1.get_high_threshold(), 0, 127)
							# self.port.send(mido.Message(type='note_on', note = 48, velocity = self.m0))
							#                               midi_queue.put( {'type':'note_on', 'note':32 , 'velocity': self.m0})
							self.beat_control_queue.put('beat')
							note_one_count = note_one_count + 1
							pulse_width = time.time() - last_time
							# self.response_queue.put(f'| {self.a0}  === {self.bright_a0.get_low_threshold()}, {self.bright_a0.get_mid_threshold()}, {self.bright_a0.get_high_threshold()} = { self.bright_a0.get_high_threshold() - self.bright_a0.get_low_threshold() } = {self.samples - last_sample } Sp {av} { pulse_width:.3f}secs { 1 / pulse_width:.4f}Hz { 60 / pulse_width:.1f}BPM { note_one_count } {note_two_count} ')
							last_time = time.time()
							last_sample = self.samples
							encoder.writeLEDB(100)
							
							note_on = not note_on
							
						elif self.a0 > self.bright_a0.get_mid_threshold() and not note_on:
							self.m0 = map_range(self.a0, self.bright_a0.get_low_threshold(), self.bright_a0.get_high_threshold(), 0, 127)
							self.m1 = map_range(self.a1, self.bright_a1.get_low_threshold(), self.bright_a1.get_high_threshold(), 0, 127)
							#                                    midi_queue.put( {'type':'note_off', 'note':32, 'velocity': self.m0})
							# self.port.send(mido.Message(type='note_on', note = 50, velocity = self.m0))
							# self.response_queue.put(f'| {self.a0}  === {self.bright_a0.get_low_threshold()}, {self.bright_a0.get_mid_threshold()}, {self.bright_a0.get_high_threshold()} == { self.bright_a0.get_high_threshold() - self.bright_a0.get_low_threshold() } ==')
							self.beat_control_queue.put('beat')
							note_two_count = note_two_count + 1
							note_on = not note_on
							encoder.writeLEDB(0)
						
						# if self.a0 == self.bright_a0.get_mid_threshold():
							# self.response_queue.put(f'threshold hit {self.a0}')
							
						# self.port.send(mido.Message(type='control_change',control = 100 , value = self.m0))
						# self.port.send(mido.Message(type='control_change',control = 101 , value = self.m1))
						
						delay = ADC_loop_delay - (time.time() % ADC_loop_delay)
						
						#logging.info('Generated a tick of %s secs with delay of %0.6f  Ticks: %s ' % (tick_delay, delay, self.tick_count))
						#logging.info('sleep delay:-%s' % (delay,))
					
						time.sleep(delay)  # Correct for drift, ensuring exact timing
						# time.sleep(.0078125)
						
				if ADC_toggle_mode:
					logging.info(f'| {self.dull_a0.display()} | {self.bright_a0.display()} | {self.dull_a1.display()} | {self.bright_a1.display()} |')
				
					self.dull_a0.reset_max_min()
					self.bright_a0.reset_max_min()
					self.dull_a1.reset_max_min()
					self.bright_a1.reset_max_min()
				else:
					self.response_queue.put(f'| {self.bright_a0.display()}  |>    {self.bright_a0.get_low_threshold()}, {self.bright_a0.get_low_threshold()}, {self.bright_a0.get_high_threshold()} { note_one_count }  | {note_two_count} <| {self.samples}')
					
					self.bright_a0.reset_max_min()
					self.bright_a1.reset_max_min()
					
						
			except ValueError:
				logging.info(f'Mapping value out of Range!!!    {self.a0}  Get Min { self.bright_a0.get_min() } Get Max { self.bright_a0.get_max() } ') 
				
			except Full:
				logging.info(f'-----------------------------------------------------{self.beat_control_queue.qsize()} Tick THREAD FULL!!!!!!!!')
			except Empty:
				logging.info(f'Thread {self.thread_id} did not receive a tick within timeout')
				# continue
			except Exception as e:
				logging.info(f'Exception: {e}')
				exc_type, exc_obj, exc_tb = sys.exc_info()
				fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
				print(exc_type, fname, exc_tb.tb_lineno)
				
			# time.sleep(.01)
			
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
	beat_control_queue = Queue()
	midi_queue = Queue()
	
	
	# response_queue2 = Queue()
	# encoder_queue = Queue()
	
	# Event to signal threads to stop
	done_event = threading.Event()
	close_logging = threading.Event()

	# Create instances
	tick_gen = BeatGenerator(beat_queue, midi_queue, beat_control_queue, done_event)
	MIDI_helper = MIDIThread(1, midi_queue, chime_queue, response_queue, done_event)
	encoder_helper = EncoderThread( response_queue, done_event)
	#chain_helper = ChimeThread(2, chime_queue, response_queue, done_event)
	monitor_helper = MonitorThread(3, beat_control_queue, midi_queue, response_queue, done_event)
	
	# RGBEncoder_listener = EncoderListenThread(3, beat_queue, encoder_queue, done_event)
	# Calibrate_helper = MeasureThread(2, calibrate_queue, response_queue2, done_event)
	# Distance_helper = DistanceThread(4, beat_queue, response_queue1, done_event)

	# Create threads
	log_thread = threading.Thread(target=log_consumer, args=(close_logging,), name="LogConsumer")
	beat_generator = threading.Thread(target=tick_gen.run, name="BeatGenerator")
	MIDI_consumer = threading.Thread(target=MIDI_helper.run, name=f"MIDI Consumer")
	#chime_consumer = threading.Thread(target=chain_helper.run, name= "chain_consumer")
	monitor_generator = threading.Thread(target=monitor_helper.run, name="monitor helper")
	encoder_generator = threading.Thread(target=encoder_helper.run, name="Encoder Helper")
	
	# rgb_encoder_listener = threading.Thread(target=RGBEncoder_listener.listen, name=f"RGBEncoder-listener Thread-{2}")
	# calibration_runner = threading.Thread(target=Calibrate_helper.calibrate, name=f"MeasureThread-{1}")

	# distance_generator = threading.Thread(target=Distance_helper.run, name="Distance_generator")

	# Start threads
	log_thread.start()
	beat_generator.start()
	MIDI_consumer.start()
	#chime_consumer.start()
	monitor_generator.start()
	encoder_generator.start()
	
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
					logging.info(f'{response}')
					
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
				
			except Exception as e:
				logging.info(f'Exception: {e}')
				exc_type, exc_obj, exc_tb = sys.exc_info()
				fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
				print(exc_type, fname, exc_tb.tb_lineno)

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
		beat_generator.join()
		monitor_generator.join()
		MIDI_consumer.join()
		encoder_generator.join()
		
		# rgb_encoder_listener.join()
		# calibration_runner.join()
		# distance_generator.join()
		
		close_logging.set()
		log_thread.join()  # Wait for the log thread to finish
		logging.info("All threads have been stopped")
	
