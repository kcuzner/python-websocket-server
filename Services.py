"""Some basic classes for services to use for implementation"""

import multiprocessing

class Service(multiprocessing.Process):
    """Base class for all services.
    
    Services should implement a run() method which is an extension of the Process
    class. In this method there should be a main loop which exits when the shutdownflag
    is set. The service should constantly watch the clientConnQueue since this is
    where clients will enter the service from. The clients are of the class WebSocketClient"""
    def __init__(self, sendQueue, recvQueue):
        multiprocessing.Process.__init__(self)
        self.sendQueue = sendQueue
        self.recvQueue = recvQueue
        self.shutdownFlag = multiprocessing.Event()


class Subscribable:
    """Base class which implements a basic subscription-based event system. This
    is inspired in part by the subscription system used in knockoutjs.
    
    When a subscriber subscribes to this class, it must pass along an method to
    call when an event happens to the subscribable."""
    
    class Subscriber:
        """Holds data about a subscriber"""
        def __init__(self, obj, callback):
            self.obj = obj
            self.callback = callback
    
    class SubscriptionEvent:
        """Event that is sent to all the subscribers. This is meant to be filled
        with an unique event ID that the subscribers will understand and some
        arbitrary data to describe the event."""
        def __init__(self, eventId, data):
            self.eventId = eventId
            self.data = data
    
    def __init__(self):
        """Initializes a subscribable"""
        self.subscribers = {}
        self.silentSubscribers = {} #these subscribers are not included in the subscriber count
        self.__currentId = 0 #this will be incremeted every time a subscriber is added and is used as a reference
    
    def getNumSubscribers(self):
        """Returns the number of subscribers to this subscribable"""
        return len(self.subscribers)
    
    def subscribe(self, obj, callback):
        """Subscribes an object with the given callback to ourselves. This method
        should be overriden in the derived classes and called from there to give
        behaviour to the event of a subscription if this is needed. Returns a
        subscription id which can be used to unsubscribe the object."""
        self.subscribers[self.__currentId] = Subscribable.Subscriber(obj, callback)
        self.__currentId = self.__currentId + 1
        return self.__currentId - 1
        
    def subscribeSilent(self, obj, callback):
        """Subscribes an object to the "silent" list. Subscribers on the silent
        list are not included in the total subscriber count. This is meant for
        global or system listeners/subscribers."""
        self.silentSubscribers[self.__currentId] = Subscribable.Subscriber(obj, callback)
        self.__currentId = self.__currentId + 1
        return self.__currentId - 1
    
    def unsubscribe(self, subscriptionId):
        """Removes the subscription with the given ID. This method should be overriden
        in derived classes and called from there to give behaviour to the event of
        unsubscription. Returns the object that was subscribed or none."""
        ret = None
        try:
            ret = self.subscribers.pop(subscriptionId).obj
        except KeyError:
            #try it on the silent subscribers
            try:
                ret = self.silentSubscribers.pop(subscriptionId).obj
            except KeyError:
                pass
        return ret
    
    def sendEvent(self, event):
        """Sends the passed events to all the subscribed objects"""
        for o in self.subscribers:
            self.subscribers[o].callback(event)
        for o in self.silentSubscribers:
            self.silentSubscribers[o].callback(event)


