import threading
import time
from queue import Queue, Empty
import logging

# Configure logging to use a queue
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

def log_consumer():
    while True:
        try:
            record = logging_queue.get(timeout=0.1)
            print(record)  # Here we just print to stdout, but you could write to a file or another logging destination
        except Empty:
            pass

class TickGenerator:
    def __init__(self, tick_queue, done_event):
        self.tick_queue = tick_queue
        self.done_event = done_event

    def run(self):
        while not self.done_event.is_set():
            self.tick_queue.put('Tick')
            logging.info('Generated a tick')
            time.sleep(1)

class HelperThread:
    def __init__(self, thread_id, tick_queue, response_queue, done_event):
        self.thread_id = thread_id
        self.tick_queue = tick_queue
        self.response_queue = response_queue
        self.done_event = done_event

    def run(self):
        while not self.done_event.is_set():
            try:
                tick = self.tick_queue.get(timeout=2)
                logging.info(f'Received tick: {tick}')
                
                # Perform some action (simulated here with a delay)
                time.sleep(0.5 + self.thread_id * 0.1)
                logging.info(f'Completed action for tick: {tick}')
                
                # Send response
                self.response_queue.put(f'Thread {self.thread_id} completed action')
            except Empty:
                logging.info(f'Thread {self.thread_id} did not receive a tick within timeout')
                continue

if __name__ == "__main__":
    # Setup communication queues
    tick_queue = Queue()
    response_queue1 = Queue()
    response_queue2 = Queue()
    
    # Event to signal threads to stop
    done_event = threading.Event()

    # Create queue instances
    tick_gen = TickGenerator(tick_queue, done_event)
    helper1 = HelperThread(1, tick_queue, response_queue1, done_event)
    helper2 = HelperThread(2, tick_queue, response_queue2, done_event)

    # Create threads
    tick_generator = threading.Thread(target=tick_gen.run, name="TickGenerator")
    helper_thread1 = threading.Thread(target=helper1.run, name=f"HelperThread-{1}")
    helper_thread2 = threading.Thread(target=helper2.run, name=f"HelperThread-{2}")
    log_thread = threading.Thread(target=log_consumer, name="LogConsumer")

    # Start threads
    tick_generator.start()
    helper_thread1.start()
    helper_thread2.start()
    log_thread.start()

    try:
        while True:
            # Check responses from helper threads
            try:
                response1 = response_queue1.get_nowait()
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
        tick_generator.join()
        helper_thread1.join()
        helper_thread2.join()
        # Note: log_thread is left running to process any remaining log messages
