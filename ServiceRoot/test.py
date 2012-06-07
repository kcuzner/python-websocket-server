
import Services
import time

class Service(Services.Service):
    
    def __init__(self, manager):
        Services.Service.__init__(self, manager)
    
    def run(self):
        print "test Service started"
        try:
            while self.shutdownFlag.is_set() == False:
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        print "test Service shutting down"