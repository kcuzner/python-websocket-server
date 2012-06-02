
import Services
import time
import Queue

class Service(Services.Service):
    
    def __init__(self):
        Services.Service.__init__(self)
    
    def run(self):
        print "Service started"
        try:
            while self.shutdownFlag.is_set() == False:
                try:
                    client = self.clientConnQueue.get_nowait()
                    print "Got client from", client.address
                    self.clientConnQueue.task_done()
                except Queue.Empty:
                    pass
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        print "Service shutting down"
