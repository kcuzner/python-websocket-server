
import socket
import WebSockets
import threading
import json
import select
import time

class Chatter(WebSockets.WebSocketHandler, threading.Thread):
    """Object representing someone who is chatting. This runs as a separate thread"""
    STATE_INITIALIZE = 0
    STATE_CHATTING = 1
    def __init__(self, conn, addr, chatrooms, chatroomsLock, stopFlag):
        """Creates a new chatter with a connection, address, chatrooms, and stop flag
        conn, addr: returned from socket.accept
        chatrooms: dict of all the chatrooms available
        chatroomsLock: threading.Lock for modifying the chatrooms dictinoary
        stopFlag: is a threading.Event object that should be set when this thread
        needs to stop"""
        WebSockets.WebSocketHandler.__init__(self, conn, addr) #call our underlying constructor
        threading.Thread.__init__(self)
        self.chatrooms = chatrooms
        self.chatroomsLock = chatroomsLock
        self.stopFlag = stopFlag
        self.connectionLock = threading.Lock() #this lock is used for the connection object
        self.state = Chatter.STATE_INITIALIZE
        self.name = ""
    
    def _handleReception(self, received):
        """Handles a received json string from the client"""
        #find out what they sent us
        data = json.loads(received)
        if self.state == Chatter.STATE_INITIALIZE:
            #we only want a name given
            if "type" in data:
                if data["type"] == "name" and "name" in data:
                    #they are giving us a name
                    self.name = data["name"]
                    self.state = Chatter.STATE_CHATTING #we are now chatting
                    print self.name, " now chatting."
                    return
            #only ask for a name if they sent us something else
            with self.connectionLock:
                self.send(json.dumps({ 'type' : 'query', 'query' : 'name' }))
        elif self.state == Chatter.STATE_CHATTING:
            if "type" in data:
                if data["type"] == "message" and "message" in data and "chatroom" in data:
                    #sending a message
                    if data["chatroom"] in self.chatrooms:
                        with self.chatrooms[data["chatroom"]].lock:
                            self.chatrooms[data["chatroom"]].message(self.name, data["message"])
                elif data["type"] == "join" and "chatroom" in data:
                    #joining a chatroom
                    if data["chatroom"] in self.chatrooms:
                        with self.chatrooms[data["chatroom"]].lock:
                            self.chatrooms[data["chatroom"]].subscribe(self, self.name)
                        self.send(json.dumps({ 'type' : 'join' , 'id' : self.chatrooms[data["chatroom"]].id}))
                elif data["type"] == "leave" and "chatroom" in data:
                    #leaving a chatroom
                    pass
                elif data["type"] == "create" and "name" in data:
                    #creating a chatroom...also joins it
                    chatroom = Chatroom(data["name"])
                    with self.chatroomsLock:
                        #add the chatroom to the list
                        self.chatrooms[chatroom.id] = chatroom
                    with chatroom.lock:
                        #subscribe to the chatroom
                        chatroom.subscribe(self, self.name)
                    with self.connectionLock:
                        self.send(json.dumps({ 'type' : 'chatroom', 'chatroom' : { 'id' : chatroom.id, 'name': chatroom.name}}))
                        self.send(json.dumps({ 'type' : 'join', 'id' : chatroom.id}))
                elif data["type"] == "list":
                    #listing all chatrooms
                    cList = []
                    for i in self.chatrooms:
                        cList.append({ "id" : i, "name" : self.chatrooms[i].name })
                    with self.connectionLock:
                        #send our list
                        self.send(json.dumps({ 'type' : 'list', 'list' : cList }))
    
    def run(self):
        """Main thread event"""
        #ask the client for a name
        data = { "type" : "query", "query" : "name"}
        with self.connectionLock:
            self.send(json.dumps(data))
        rlist = [ self.connection ]
        while self.stopFlag.isSet() is not True:
            #check for write events
            received = False
            with self.connectionLock:
                r, w, x = select.select(rlist, [], [], 0)
                if r:
                    received = self.recv()
            if received:
                self._handleReception(received)
            time.sleep(0.05) #wait for 50ms
    
    def onChatroomEvent(self, event):
        """Called by the chatroom object to tell us something"""
        if self.state == Chatter.STATE_CHATTING:
            #we ignore events unless we are chatting
            if event.event == Chatroom.ChatroomEvent.EV_MESSAGE:
                with self.connectionLock:
                    data = { 'type' : 'event', 'event' : { 'type' : 'message', 'name' : event.data[0], 'message' : event.data[1] } }
                    self.send(json.dumps(data))
            elif event.event == Chatroom.ChatroomEvent.EV_NEWSUBSCRIBER:
                with self.connectionLock:
                    data = { 'type' : 'event', 'event' : { 'type' : 'newuser', 'name' : event.data } }
                    self.send(json.dumps(data))
            

class Chatroom:
    """Object encapsulating a chatroom"""
    idLock = threading.Lock() #lock for the static id variable since it could be accessed from many places
    nextId = 0
    class ChatroomEvent:
        """Encapsulates an event happening in the chatroom"""
        EV_MESSAGE = 0
        EV_NEWSUBSCRIBER = 1
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
        self.id = Chatroom.lastId
        with Chatroom.idLock:
            Chatroom.lastId = Chatroom.lastId + 1
    
    def subscribe(self, obj, name):
        """Subscribes an object to this chatroom's events. The subscriber should implement a
        method called onChatroomEvent(event) where the argument is a Chatroom.ChatEvent"""
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_NEWSUBSCRIBER, name)
        self._sendEvent(event)
        self.subscribers.append(obj)
    
    def message(self, name, message):
        """Places a message into the chatroom"""
        event = Chatroom.ChatroomEvent(Chatroom.ChatroomEvent.EV_MESSAGE, (name, message))
        self._sendEvent(event)
    
    def _sendEvent(self, event):
        """Sends the event object to everyone"""
        for subscriber in self.subscribers:
            subscriber.onChatroomEvent(event)

def main():
    HOST = ""
    PORT = 12345
    ADDR = (HOST, PORT)

    server = socket.socket( socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server.bind(ADDR)
    server.listen(5)
    
    chatroomsLock = threading.Lock()
    chatrooms = dict()
    
    stopFlag = threading.Event()
    
    print "Listening"
    go = True
    while(go):
        try:
            conn,addr = server.accept();
            print "Client from ", addr
            client = Chatter(conn, addr, chatrooms, chatroomsLock, stopFlag)
            client.start()
        except KeyboardInterrupt:
            go = False
    
    print "Closing down..."
    stopFlag.set()
    server.shutdown(socket.SHUT_RDWR)
    server.close()

if __name__ == "__main__":
    main()
