

import Services
import Queue
import time
import threading
import json

class Chatter(threading.Thread):
    STATE_INITIALIZE = 0
    STATE_SELECTING = 1
    STATE_CHATTING = 2
    def __init__(self, client, chatrooms, shutdownFlag):
        threading.Thread.__init__(self)
        self.clientLock = threading.Lock()
        self.client = client
        self.chatrooms = chatrooms
        self.chatroom = None
        self.shutdownFlag = shutdownFlag
        self.state = Chatter.STATE_INITIALIZE
        self.client.handle_recv = self._handleReception
    
    def run(self):
        data = { "type" : "query", "query" : "name"}
        with self.clientLock:
            self.client.send(json.dumps(data))
        while self.shutdownFlag.is_set() == False:
            if self.client.open == False:
                #we got closed on the client side probably
                self.chatrooms.unsubscribe(self)
                if self.chatroom != None:
                    self.chatroom.unsubscribe(self, self.name)
                break
            time.sleep(0.05) #wait for 50ms
            
    def _handleReception(self):
        """Handles a received json string from the client"""
        #find out what they sent us
        while self.client.recvQueue.empty() == False:
            received = self.client.recvQueue.get()
            data = json.loads(received)
            if self.state == Chatter.STATE_INITIALIZE:
                #we only want a name given
                if "type" in data:
                    if data["type"] == "name" and "name" in data:
                        #they are giving us a name
                        self.name = data["name"]
                        self.state = Chatter.STATE_SELECTING #we are now selecting a chatroom
                        print self.name, " now chatting."
                        return
                #only ask for a name if they sent us something else
                with self.clientLock:
                    self.client.send(json.dumps({ 'type' : 'query', 'query' : 'name' }))
            if self.state == Chatter.STATE_CHATTING or self.state == Chatter.STATE_SELECTING:
                #in selection mode or chatting mode
                if "type" in data:
                    if data["type"] == "join" and "chatroom" in data:
                        #subscribe to a chatroom
                        toJoin = None
                        ret = { 'type' : 'join', 'chatroom' : data["chatroom"] }
                        if data["chatroom"] in self.chatrooms.chatrooms:
                            toJoin = self.chatrooms.chatrooms[data["chatroom"]]
                        if toJoin == None:
                            ret = { 'type' : 'notice', 'notice' : 'Chatroom ' + str(data["chatroom"]) + ' not found.' }
                        elif self.chatroom != None:
                            #unsubscribe from our previous chatroom
                            self.chatroom.unsubscribe(self, self.name)
                        if toJoin != None:
                            #subscribe to the new chatroom
                            self.chatroom = toJoin
                            self.chatroom.subscribe(self, self.name)
                            self.state = Chatter.STATE_CHATTING
                        with self.clientLock:
                            self.client.send(json.dumps(ret))
                    if data["type"] == "create" and "chatroom" in data:
                        #create a new chatroom
                        self.chatrooms.createChatroom(data["chatroom"]) #if this works, a chatroom event will happen
            if self.state == Chatter.STATE_CHATTING:
                if "type" in data:
                    if data["type"] == "message" and "message" in data:
                        #sending a message
                        with self.chatroom.lock:
                            self.chatroom.message(self.name, data["message"])

    def onChatroomEvent(self, event):
        """Called by the chatroom object to tell us something"""
        if event.event == Chatroom.ChatroomEvent.EV_LISTING:
            #they are listing all their rooms to us
            with self.clientLock:
                data = { 'type' : 'event', 'event' : { 'type' : 'listing', 'chatrooms' : event.data } }
                self.client.send(json.dumps(data))
            
        if self.state == Chatter.STATE_CHATTING or self.state == Chatter.STATE_SELECTING:
            #we ignore some events unless we are chatting
            if event.event == Chatroom.ChatroomEvent.EV_MESSAGE:
                with self.clientLock:
                    data = { 'type' : 'event', 'event' : { 'type' : 'message', 'name' : event.data[0], 'message' : event.data[1] } }
                    self.client.send(json.dumps(data))
            elif event.event == Chatroom.ChatroomEvent.EV_NEWSUBSCRIBER:
                with self.clientLock:
                    data = { 'type' : 'event', 'event' : { 'type' : 'newuser', 'name' : event.data } }
                    self.client.send(json.dumps(data))
            elif event.event == Chatroom.ChatroomEvent.EV_UNSUBSCRIBE:
                with self.clientLock:
                    data = { 'type' : 'event', 'event' : { 'type' : 'logoff', 'name' : event.data } }
                    self.client.send(json.dumps(data))
            elif event.event == Chatroom.ChatroomEvent.EV_CREATE:
                with self.clientLock:
                    data = { 'type' : 'event', 'event' : { 'type' : 'newchatroom', 'name' : event.data } }
                    self.client.send(json.dumps(data))

class ChatroomCollection:
    """A list of chatrooms that supports "subscriptions" """
    def __init__(self):
        self.chatrooms = {}
        self.subscribers = []
    
    def subscribe(self, chatter):
        """Subscribes a chatter to the events in this chatroom collection (such as adding chatrooms)"""
        self.subscribers.append(chatter)
        #tell the chatter about all my rooms
        names = []
        for room in self.chatrooms:
            names.append(room)
        chatter.onChatroomEvent(Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_LISTING, names))
    
    def unsubscribe(self, chatter):
        """Unsubscribes a chatter from the events in this chatroom collection"""
        try:
            self.subscribers.remove(chatter)
        except ValueError:
            pass
    
    def createChatroom(self, name):
        """Creates a chatroom and informs all subscribers it has been created"""
        if name in self.chatrooms:
            print "oh noes"
            return False #chatroom already exists
        self.chatrooms[name] = Chatroom(name)
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_CREATE, name)
        for subscriber in self.subscribers:
            subscriber.onChatroomEvent(event)
        return True

class Chatroom:
    class ChatroomEvent:
        """Encapsulates an event happening in the chatroom"""
        EV_MESSAGE = 0
        EV_NEWSUBSCRIBER = 1
        EV_UNSUBSCRIBE = 2
        EV_CREATE = 3
        EV_LISTING = 4
        def __init__(self, event, data):
            """Creates a new chatroom event.
            type: A value matching one of the EV_ variables in this class
            data: Data to go along with the event. If a message event, it will contain the tuple with the message data.
                  If a new subscriber event, it contains the new subscriber's name"""
            self.event = event
            self.data = data
    
    def __init__(self, name):
        self.subscribers = list()
        self.lock = threading.Lock()
        self.name = name
        
    
    def subscribe(self, obj, name):
        """Subscribes an object to this chatroom's events. The subscriber should implement a
        method called onChatroomEvent(event) where the argument is a Chatroom.ChatEvent"""
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_NEWSUBSCRIBER, name)
        self._sendEvent(event)
        self.subscribers.append(obj)
    
    def unsubscribe(self, obj, name):
        try:
            self.subscribers.remove(obj)
            event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_UNSUBSCRIBE, name)
            self._sendEvent(event)
        except ValueError:
            pass
    
    def message(self, name, message):
        """Places a message into the chatroom"""
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_MESSAGE, (name, message))
        self._sendEvent(event)
    
    def _sendEvent(self, event):
        """Sends the event object to everyone"""
        for subscriber in self.subscribers:
            subscriber.onChatroomEvent(event)


class Service(Services.Service):
    def __init__(self):
        Services.Service.__init__(self)
        self.chatrooms = ChatroomCollection()
    
    def run(self):
        """Main thread method"""
        print "Chatroom Service started"
        try:
            while self.shutdownFlag.is_set() == False:
                try:
                    client = self.clientConnQueue.get_nowait()
                    print "Got client from", client.address
                    chatter = Chatter(client, self.chatrooms, self.shutdownFlag)
                    chatter.start()
                    self.chatrooms.subscribe(chatter)
                    self.clientConnQueue.task_done()
                except Queue.Empty:
                    pass
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        print "Chatroom Service shutting down"
        
