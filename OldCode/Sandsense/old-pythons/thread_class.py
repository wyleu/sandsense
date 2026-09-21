import threading
import time
from queue import Queue, Empty

class TickGenerator:
    def __init__(self, tick_queue, done_event):
        self.tick_queue = tick_queue
        self.done_event = done_event

    def run(self):
        while not self.done_event.is_set():
            self.tick_queue.put('Tick')
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
                print(f'Thread {self.thread_id} received: {tick}')
                
                # Perform some action (simulated here with a delay)
                time.sleep(0.5 + self.thread_id * 0.1)
                
                # Send response
                self.response_queue.put(f'Thread {self.thread_id} completed action')
            except Empty:
                continue

if __name__ == "__main__":
    # Setup communication queues
    tick_queue = Queue()
    response_queue1 = Queue()
    response_queue2 = Queue()
    
    # Event to signal threads to stop
    done_event = threading.Event()

    # Create instances
    tick_gen = TickGenerator(tick_queue, done_event)
    helper1 = HelperThread(1, tick_queue, response_queue1, done_event)
    helper2 = HelperThread(2, tick_queue, response_queue2, done_event)

    # Create threads
    main_thread = threading.Thread(target=tick_gen.run)
    helper_thread1 = threading.Thread(target=helper1.run)
    helper_thread2 = threading.Thread(target=helper2.run)

    # Start threads
    main_thread.start()
    helper_thread1.start()
    helper_thread2.start()

    try:
        while True:
            # Check responses from helper threads
            try:
                response1 = response_queue1.get_nowait()
                print(response1)
            except Empty:
                pass

            try:
                response2 = response_queue2.get_nowait()
                print(response2)
            except Empty:
                pass
            
            time.sleep(0.1)  # Small delay to reduce CPU usage
    
    except KeyboardInterrupt:
        print("Stopping threads...")
        done_event.set()
        main_thread.join()
        helper_thread1.join()
        helper_thread2.join()
