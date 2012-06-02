
import threading
import Queue

class Service(threading.Thread):
    """Base class for all services.
    
    Services should implement a run() method which is an extension of the Process
    class. In this method there should be a main loop which exits when the shutdownflag
    is set. The service should constantly watch the clientConnQueue since this is
    where clients will enter the service from. The clients are of the class WebSocketClient"""
    def __init__(self):
        threading.Thread.__init__(self)
        self.clientConnQueue = Queue.Queue()
        print self.clientConnQueue
        self.shutdownFlag = threading.Event()
