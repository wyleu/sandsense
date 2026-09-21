import time
from datetime import datetime
import random
import logging

class TickSimulator:
    def __init__(self):
        self.counter = 0
        self.last_tick_time = time.time()
        self.state = "idle"
        self.cpu_usage = 0.0
        self.memory_usage = 0.0
        self.events = []
        
        # Setup logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

    def simulate_resource_usage(self):
        self.cpu_usage = random.uniform(1.0, 100.0)  # Simulate CPU usage in percentage
        self.memory_usage = random.uniform(10.0, 1000.0)  # Simulate memory usage in MB

    def handle_events(self):
        if random.random() < 0.05:  # 5% chance of an event occurring
            event = random.choice(["alert", "maintenance", "upgrade"])
            self.events.append(event)
            self.logger.info(f"Event triggered: {event}")

    def update_state(self):
        if "alert" in self.events:
            self.state = "alert"
        elif "maintenance" in self.events:
            self.state = "maintenance"
        elif "upgrade" in self.events:
            self.state = "upgrade"
        elif self.cpu_usage > 80 or self.memory_usage > 800:
            self.state = "warning"
        elif self.cpu_usage > 95 or self.memory_usage > 900:
            self.state = "critical"
        else:
            self.state = "idle"

    def tick(self):
        current_time = time.time()
        elapsed = current_time - self.last_tick_time

        self.simulate_resource_usage()
        self.handle_events()
        self.update_state()

        # Log the tick
        log_entry = f"Tick #{self.counter}: State: {self.state}, CPU: {self.cpu_usage:.2f}%, Memory: {self.memory_usage:.2f}MB, Elapsed: {elapsed:.4f} seconds"
        self.logger.info(log_entry)

        # Perform actions based on state
        if self.state == "alert":
            self.logger.warning("System in alert state - check logs!")
        elif self.state == "maintenance":
            self.logger.info("System undergoing maintenance")
        elif self.state == "upgrade":
            self.logger.info("System upgrade in progress")
        elif self.state == "warning":
            self.logger.warning("High resource usage detected")
        elif self.state == "critical":
            self.logger.error("System in critical state - immediate action required!")

        # Simulate processing time based on CPU usage
        processing_time = self.cpu_usage / 100  # More CPU usage, longer processing
        time.sleep(processing_time)

        self.events = []  # Clear events for the next tick
        self.counter += 1
        self.last_tick_time = current_time

def main():
    simulator = TickSimulator()
    delay = 60.0 / 48  # seconds, for 48 ticks per minute
    
    for _ in range(48 * 60):  # Run for an hour
        simulator.tick()
        time.sleep(delay)

if __name__ == "__main__":
    main()
