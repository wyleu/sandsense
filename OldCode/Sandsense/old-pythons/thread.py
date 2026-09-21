import threading
import time
from queue import Queue, Empty

def tick_generator(tick_queue, done_event):
    while not done_event.is_set():
        tick_queue.put('Tick')
        time.sleep(1)

def helper_thread(thread_id, tick_queue, response_queue, done_event):
    while not done_event.is_set():
        try:
            tick = tick_queue.get(timeout=2)  # Increased timeout to ensure both threads get a chance
            print(f'Thread {thread_id} received: {tick}')
            
            # Perform some action (simulated here with a delay)
            time.sleep(0.5 + thread_id * 0.1)  # Different delay for each thread
            
            # Send response
            response_queue.put(f'Thread {thread_id} completed action')
        except Empty:
            # If no tick was received within timeout, continue loop
            continue

if __name__ == "__main__":
    # Setup communication queues
    tick_queue = Queue()
    response_queue1 = Queue()
    response_queue2 = Queue()
    
    # Event to signal threads to stop
    done_event = threading.Event()

    # Create threads
    main_thread = threading.Thread(target=tick_generator, args=(tick_queue, done_event))
    helper1 = threading.Thread(target=helper_thread, args=(1, tick_queue, response_queue1, done_event))
    helper2 = threading.Thread(target=helper_thread, args=(2, tick_queue, response_queue2, done_event))

    # Start threads
    main_thread.start()
    helper1.start()
    helper2.start()

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
        helper1.join()
        helper2.join()
