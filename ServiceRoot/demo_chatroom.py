
import WebSockets
import Services
import Queue
import time
import threading
import json

from WebSockets import WebSocketTransaction

class Chatter:
    STATE_INITIALIZE = 0
    STATE_SELECTING = 1
    STATE_CHATTING = 2
    def __init__(self, addr, socketId, sendQueue, chatrooms):
        self.address = addr;
        self.socketId = socketId
        self.sendQueue = sendQueue;
        self.chatrooms = chatrooms
        self.chatroom = None
        self.subscriptionId = None
        self.state = Chatter.STATE_INITIALIZE
        data = { "type" : "query", "query" : "name"}
        transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, self.socketId, json.dumps(data))
        self.sendQueue.put(transaction)
            
    def injectReceived(self, received):
        """Handles a received json string from the client"""
        #validate the packet
        if received.socketId != self.socketId:
            print "Received a packet meant for", received.socketId
            return #we can't process this one
        #find out what they sent us
        if received.transactionType == WebSocketTransaction.TRANSACTION_CLOSE:
            print "Woe is me for I am undone"
            return
        #if we made it this far, it was normal data being received
        data = json.loads(received.data)
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
            transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, self.socketId, json.dumps({ 'type' : 'query', 'query' : 'name' }))
            self.sendQueue.put(transaction)
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
                        self.chatroom.unsubscribe(self.subscriptionId)
                    if toJoin != None:
                        #subscribe to the new chatroom
                        self.chatroom = toJoin
                        self.subscriptionId = self.chatroom.subscribe(self)
                        self.state = Chatter.STATE_CHATTING
                    transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, self.socketId, json.dumps(ret))
                    self.sendQueue.put(transaction)
                if data["type"] == "create" and "chatroom" in data:
                    #create a new chatroom
                    self.chatrooms.createChatroom(data["chatroom"]) #if this works, a chatroom event will happen
        if self.state == Chatter.STATE_CHATTING:
            if "type" in data:
                if data["type"] == "message" and "message" in data:
                    #sending a message
                    with self.chatroom.lock:
                        self.chatroom.message(self, data["message"])
    
    def onClose(self):
        """Called when the underlying socket is closed"""
        print "I was closed..."

    def onChatroomEvent(self, event):
        """Called by the chatroom object to tell us something"""
        data = {}
        if event.eventId == Chatroom.ChatroomEvent.EV_LISTING:
            #they are listing all their rooms to us
            data = { 'type' : 'event', 'event' : { 'type' : 'listing', 'chatrooms' : event.data } }
        elif self.state == Chatter.STATE_CHATTING or self.state == Chatter.STATE_SELECTING:
            #we ignore some events unless we are chatting
            if event.eventId == Chatroom.ChatroomEvent.EV_MESSAGE:
                data = { 'type' : 'event', 'event' : { 'type' : 'message', 'name' : event.data[0], 'message' : event.data[1] } }
            elif event.eventId == Chatroom.ChatroomEvent.EV_NEWSUBSCRIBER:
                data = { 'type' : 'event', 'event' : { 'type' : 'newuser', 'name' : event.data } }
            elif event.eventId == Chatroom.ChatroomEvent.EV_UNSUBSCRIBE:
                data = { 'type' : 'event', 'event' : { 'type' : 'logoff', 'name' : event.data } }
            elif event.eventId == Chatroom.ChatroomEvent.EV_CREATE:
                data = { 'type' : 'event', 'event' : { 'type' : 'newchatroom', 'name' : event.data } }
        transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, self.socketId, json.dumps(data))
        self.sendQueue.put(transaction)

class ChatroomCollection(Services.Subscribable):
    """A list of chatrooms that supports "subscriptions" """
    def __init__(self):
        Services.Subscribable.__init__(self)
        self.chatrooms = {}
    
    def subscribe(self, chatter):
        """Subscribes a chatter to the events in this chatroom collection (such as adding chatrooms)"""
        ret = Services.Subscribable.subscribe(self, chatter, chatter.onChatroomEvent)
        #tell the chatter about all my rooms
        names = []
        for room in self.chatrooms:
            names.append(room)
        chatter.onChatroomEvent(Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_LISTING, names))
        return ret
    
    def createChatroom(self, name):
        """Creates a chatroom and informs all subscribers it has been created"""
        if name in self.chatrooms:
            print "oh noes"
            return False #chatroom already exists
        self.chatrooms[name] = Chatroom(name)
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_CREATE, name)
        self.sendEvent(event)
        return True

class Chatroom(Services.Subscribable):
    class ChatroomEvent(Services.Subscribable.SubscriptionEvent):
        """Encapsulates an event happening in the chatroom"""
        EV_MESSAGE = 0
        EV_NEWSUBSCRIBER = 1
        EV_UNSUBSCRIBE = 2
        EV_CREATE = 3
        EV_LISTING = 4
        def __init__(self, eventId, data):
            """Creates a new chatroom event.
            type: A value matching one of the EV_ variables in this class
            data: Data to go along with the event. If a message event, it will contain the tuple with the message data.
                  If a new subscriber event, it contains the new subscriber's name"""
            Services.Subscribable.SubscriptionEvent.__init__(self, eventId, data)
    
    def __init__(self, name):
        Services.Subscribable.__init__(self)
        self.lock = threading.Lock()
        self.name = name
        
    
    def subscribe(self, chatter):
        """Subscribes a chatter to this chatroom's events. The subscriber should implement a
        method called onChatroomEvent(event) where the argument is a Chatroom.ChatroomEvent"""
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_NEWSUBSCRIBER, chatter.name)
        self.sendEvent(event)
        return Services.Subscribable.subscribe(self, chatter, chatter.onChatroomEvent)
    
    def unsubscribe(self, sId):
        name = Services.Subscribable.unsubscribe(self, sId).name
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_UNSUBSCRIBE, name)
        self.sendEvent(event)
    
    def message(self, chatter, message):
        """Places a message into the chatroom"""
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_MESSAGE, (chatter.name, message))
        self.sendEvent(event)


class Service(Services.Service):
    def __init__(self, sendQueue, recvQueue):
        Services.Service.__init__(self, sendQueue, recvQueue)
        self.chatrooms = ChatroomCollection()
        self.clients = {}
    
    def run(self):
        """Main thread method"""
        print "Chatroom Service started"
        try:
            while self.shutdownFlag.is_set() == False:
                try:
                    transaction = self.recvQueue.get_nowait()
                    self.recvQueue.task_done()
                    if transaction.transactionType == WebSockets.WebSocketTransaction.TRANSACTION_NEWSOCKET:
                        #we have a new client!
                        print "Got client from", transaction.data
                        chatter = Chatter(transaction.data, transaction.socketId, self.sendQueue, self.chatrooms)
                        self.chatrooms.subscribe(chatter)
                        self.clients[chatter.socketId] = chatter                        
                    else:
                        #find the chatter to send this to
                        self.clients[transaction.socketId].injectReceived(transaction)
                except Queue.Empty:
                    pass
                except KeyError:
                    pass
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        print "Chatroom Service shutting down"
        
