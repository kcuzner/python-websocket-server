
import socket
import select
import threading
import multiprocessing
import Queue
import time
import collections
import sys
import logging

BUFFER_SIZE = 4096
WEBSOCKET_VERSION = "13"
WEBSOCKET_MAGIC_HANDSHAKE_STRING = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

class WebSocketInitializationException(Exception):
    """Raised when a web socket initialization fails due to bad handshakes or requests"""
    pass
class WebSocketUnmaskedCommunicationException(Exception):
    """Raised when a client->server communication is not masked"""
    pass
class WebSocketInvalidDataException(Exception):
    """Raised when receiving data goes horribly wrong (namely...it got something unexpected)"""
    pass

class WebSocketTransaction:
        """Contains transaction data which is passed through the queues when sending
        or receiving data to or from a socket."""
        TRANSACTION_NEWSOCKET = 0 #used on the socket notification queue to inform a service it has a new socket with the given id
        TRANSACTION_DATA = 1 #used on send/recv queues to send/receive data to/from a socket
        TRANSACTION_CLOSE = 2 #used on send/recv queues to close the socket or inform the service the socket has been closed
        def __init__(self, transactionType, socketId, data):
            self.transactionType = transactionType
            self.socketId = socketId
            self.data = data

class WebSocketClient:
    """Contains socket information about a client which is connected to the server."""
    
    class WebSocketRecvState:
            """Representation of the state of an in progress receiving operation"""
            STATE_TYPE = 0 #this state is used when the type byte is the next thing to receive
            STATE_LEN = 1 #this state is used when the length bytes still have some bytes which should be received next
            STATE_MASK = 2 #this state is used when the masks still ahve some bytes which should be received next
            STATE_PAYLOAD = 3 #this state is used when the payload still has some bytes which should be received next
            STATE_DONE = 4 #this state is used when the web socket is done receiving
            def __init__(self):
                """Initializes an initial receive state to nothing recieved yet"""
                self.typeByte = None
                self.lenBytes = bytearray()
                self.computedLength = 0
                self.maskBytes = bytearray()
                self.maskIndex = 0
                self.unmaskedPayloadBytes = bytearray()
                self.state = WebSocketClient.WebSocketRecvState.STATE_TYPE
            
            def receive(self, receivedBytes):
                """Processes some bytes into this object. Returns the unprocessed bytes.
                
                This operates as a state machine on the bytes as though they are an array. It
                processes each byte individually and changes the state depending on the value
                of the byte. In the case where there aren't enough bytes to complete a receive
                sequence (going from STATE_TYPE to STATE_DONE), it should pick up where it left
                off on the next receive."""
                byteQueue = collections.deque(receivedBytes)
                while len(byteQueue) > 0 and self.state != WebSocketClient.WebSocketRecvState.STATE_DONE:
                    b = byteQueue.popleft() #pop from the beginning like a queue
                    if self.state == WebSocketClient.WebSocketRecvState.STATE_TYPE:
                        #process this byte as the initial type declarer
                        if b != 0x81:
                            #this shouldn't be anything but 0x81
                            raise WebSocketInvalidDataException()
                        self.typeByte = b
                        self.state = WebSocketClient.WebSocketRecvState.STATE_LEN
                    elif self.state == WebSocketClient.WebSocketRecvState.STATE_LEN:
                        #process this byte as part of the length
                        if len(self.lenBytes) == 0:
                            #this is the first byte
                            if b < 0x80:
                                #it should have its 8th bit set since we need masked communication
                                raise WebSocketInvalidDataException()
                            b = b & 0x7F #unmask it
                            self.lenBytes.append(b)
                            #figure out what to do next
                            if b <= 0x7D:
                                #this is the only length byte we need. time to move on to masks
                                self.computedLength = b
                                self.state = WebSocketClient.WebSocketRecvState.STATE_MASK
                            #if we haven't changed the state by now, it needs some more information
                        elif self.lenBytes[0] == 0x7E:
                            #two bytes length (16 bits)
                            self.lenBytes.append(b)
                            if len(self.lenBytes) == 3:
                                #this was the last one
                                self.computedLength = ((self.lenBytes[1] & 0xFF) << 8 | (self.lenBytes[2] & 0xFF))
                                self.state = WebSocketClient.WebSocketRecvState.STATE_MASK
                        elif self.lenBytes[0] == 0x7F:
                            #eight bytes length (64 bits)
                            self.lenBytes.append(b)
                            if len(self.lenBytes) == 9:
                                #this was the last one
                                self.computedLength = (self.lenBytes[1] & 0xFF) << 56
                                self.computedLength |= (self.lenBytes[2] & 0xFF) << 48
                                self.computedLength |= (self.lenBytes[3] & 0xFF) << 40
                                self.computedLength |= (self.lenBytes[4] & 0xFF) << 32
                                self.computedLength |= (self.lenBytes[5] & 0xFF) << 24
                                self.computedLength |= (self.lenBytes[6] & 0xFF) << 16
                                self.computedLength |= (self.lenBytes[7] & 0xFF) << 8
                                self.computedLength |= self.lenBytes[8] & 0xFF
                                self.state = WebSocketClient.WebSocketRecvState.STATE_MASK
                    elif self.state == WebSocketClient.WebSocketRecvState.STATE_MASK:
                        #process this byte as part of the masks
                        self.maskBytes.append(b)
                        if len(self.maskBytes) == 4:
                            #all masks received
                            self.state = WebSocketClient.WebSocketRecvState.STATE_PAYLOAD
                    elif self.state == WebSocketClient.WebSocketRecvState.STATE_PAYLOAD:
                        #process this byte as part of the payload
                        b = b ^ self.maskBytes[self.maskIndex]
                        self.maskIndex = (self.maskIndex + 1) % 4
                        self.unmaskedPayloadBytes.append(b)
                        if len(self.unmaskedPayloadBytes) == self.computedLength:
                            #we are done receiving
                            self.state = WebSocketClient.WebSocketRecvState.STATE_DONE
                #process the remaining bytes into a bytearray and return it
                return bytearray(byteQueue)
    
    
    
    class WebSocketManager(threading.Thread):
        """Thread which manages communication between WebSockets and their clients.
        
        This asyncronously sends/receives data to/from sockets while at the same time
        handling the service send/recv queues in a "switchboard" like fashion."""
        
        def __init__(self, socketList, stopEvent, processDirectory):
            """Initializes a new WebSocketSendRecvThread with the given sockets,
            a multiprocessing.Event (stopEvent) to stop the thread gracefully, and
            the process directory which will contain all the processes"""
            threading.Thread.__init__(self)
            self.sockets = {} #sockets are stored sorted by their unique ids
            for sock in socketList:
                self.sockets[sock.id] = sock
            self.socketListLock = threading.Lock()
            self.stopEvent = stopEvent
            self.processDirectory = processDirectory
        
        def addWebSocket(self, s):
            """Adds a socket to the list to be asyncronously managed. Returns if it was successful"""
            if self.isAlive() == False:
                #create a new one
                return False
            else:
                #add to the existing one
                with self.socketListLock:
                    self.sockets[s.id] = s
                return True
        
        def _stringToFrame(self, data):
            """Turns a string into a WebSocket data frame. Returns a bytes(). 'data' is a string"""
            #determine the size of the data we were told to send
            rawData = data#bytearray(data, 'ascii')
            dataLength = len(rawData)
            outputBytes = bytearray()
            outputBytes.append(0x81) #0x81 = text data type
            if dataLength < 0x7D:
                #a nice short length
                outputBytes.append(len(rawData))
            elif dataLength >= 0x7E and len(rawData) < 0xFFFF:
                #two additional bytes of length needed
                outputBytes.append(0x7E)
                outputBytes.append(dataLength >> 8 & 0xFF)
                outputBytes.append(dataLength & 0xFF)
            else:
                #eight additional bytes of length needed
                outputBytes.append(0x7F)
                outputBytes.append(dataLength >> 56 & 0xFF)
                outputBytes.append(dataLength >> 48 & 0xFF)
                outputBytes.append(dataLength >> 40 & 0xFF)
                outputBytes.append(dataLength >> 32 & 0xFF)
                outputBytes.append(dataLength >> 24 & 0xFF)
                outputBytes.append(dataLength >> 16 & 0xFF)
                outputBytes.append(dataLength >> 8 & 0xFF)
                outputBytes.append(dataLength & 0xFF)
            #tack on the raw data now
            for byte in rawData:
                outputBytes.append(ord(byte))
            return bytes(outputBytes)
        
        def _sendToSocket(self, data, sock):
            """Sends some bytes to a socket and returns the remaining bytes or none if it was all sent"""
            nSent = sock.send(data)
            if nSent == len(data):
                return None
            else:
                return data[nSent:] #if we didn't send the whole thing, return from the last index sent to the end
        
        def __queueHelper(self):
            """Thread method to operate the "switchboard" between server queues and the individual socket queues"""
            while self.stopEvent.is_set() == False:
                processes = self.processDirectory.getAllProcesses()
                for pid in processes:
                    #read through the sendQueue in this process and send it to the appropriate sockets
                    process = processes[pid]
                    while process.sendQueue.empty() == False:
                        try:
                            transaction = process.sendQueue.get_nowait()
                            with self.socketListLock:
                                if transaction.socketId in self.sockets:
                                    self.sockets[transaction.socketId].sendQueue.put(transaction)                                
                        except Queue.Empty:
                            break
                #get all our sockets
                with self.socketListLock:
                    socketIds = self.sockets.keys()
                for sockId in socketIds:
                    s = self.sockets[sockId] #this is a WebSocketClient
                    try:
                        while s.recvQueue.empty() == False:
                            #put their receive queue into the approproate process
                            transaction = s.recvQueue.get_nowait()
                            processes[s.serviceId].recvQueue.put(transaction)
                            #if this was a close transaction, we need to remove it from our list
                            if transaction.transactionType == WebSocketTransaction.TRANSACTION_CLOSE:
                                with self.socketListLock:
                                    self.sockets.pop(sockId)
                    except Queue.Empty:
                        break;
                time.sleep(0.005) #sleep for 5 ms before doing this again
                
        
        def run(self):
            """Main thread method which will run until all sockets are no longer active"""
            #start the queue helper
            queueHelper = threading.Thread(target=self.__queueHelper)
            queueHelper.start()
            while self.stopEvent.is_set() == False:
                with self.socketListLock:
                    #get the list of socket ids so that we can iterate through them without eating up the socket list lock
                    #in theory, fetching an item from a dictionary in python is thread safe
                    socketIds = self.sockets.keys()
                for sockId in socketIds:
                    s = self.sockets[sockId] #these are not sockets, but WebSocket objects
                    if s.open == False:
                        #remove this socket from our list and put this event into the receive queue
                        print "Notice: Socket", s, "removed."
                        s.recvQueue.put(WebSocketTransaction(WebSocketTransaction.TRANSACTION_CLOSE, sockId, None))
                        continue #skip the rest of this
                    with s.lock: #lock the individiual socket
                        #sadly, we need to call select on every socket individually so that we can keep track of the WebSocketClient class
                        sList = [ s.connection ]
                        try:
                            r, w, x = select.select(sList, sList, sList, 0)
                        except socket.error:
                            #it failed. remove this client
                            print "Notice: Socket", s, "failed."
                            s.open = False #this is no longer open, but we can't call close since it would break it some more
                    if x:
                        print "Notice: Socket", s, "has an exceptional condition"
                        with s.lock:
                            s.open = False
                    if r:
                        #the socket is ready to be read
                        try:
                            with s.lock:
                                received = r[0].recv(4096) #we will receive up to 4096 bytes
                                receivedBytes = bytearray(received)
                                if len(receivedBytes) == 0:
                                    #the socket was gracefully closed on the other end
                                    s.close()
                                while len(receivedBytes) > 0:
                                    receivedBytes = s._readProgress.receive(receivedBytes)
                                    if s._readProgress.state == WebSocketClient.WebSocketRecvState.STATE_DONE:
                                        #a string was read, so put it in the queue
                                        try:
                                            transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, s.id, s._readProgress.unmaskedPayloadBytes.decode(sys.getdefaultencoding()))
                                            s.recvQueue.put_nowait(transaction)
                                        except Queue.Full:
                                            logging.warning("Notice: Receive queue full on WebSocketClient" + str(s) + "... did you forget to empty the queue or call task_done?")
                                            pass #oh well...I guess their data gets to be lost since they didn't bother to empty their queue
                                        s._readProgress = WebSocketClient.WebSocketRecvState() #reset the progress
                        except WebSocketInvalidDataException:
                            #The socket got some bad data, so it should be closed
                            with s.lock:
                                s.open = False
                        except socket.error:
                            pass #don't worry about it
                    if w:
                        #the socket is ready to be written
                        #for writing, the exception catcher has to be inside rather than outside
                        #everything like the received catcher was since we need to make sure to
                        #inform the sendqueue that we are done with the passed task
                        with s.lock:
                            if s._writeProgress != None:
                                #we still have something to write
                                try:
                                    s._writeProgress = self._sendToSocket(s._writeProgress, s.connection)
                                except socket.error:
                                    #probably a broken pipe. don't worry about it...it will be caught on the next loop around
                                    pass
                            elif not s.sendQueue.empty():
                                #there is something new to start sending
                                try:
                                    transaction = s.sendQueue.get_nowait()
                                    if (transaction.transactionType == WebSocketTransaction.TRANSACTION_CLOSE):
                                        #they want us to close the socket...
                                        s.close()
                                    else:
                                        #they want us to write something to the socket
                                        toWrite = self._stringToFrame(transaction.data)
                                        try:
                                            self._sendToSocket(toWrite, s.connection)
                                        except socket.error:
                                            #probably a broken pipe. don't worry about it...it will be caught on the next loop around
                                            pass
                                except Queue.Empty:
                                    pass #don't worry about it...we just couldn't get anything
                time.sleep(0.025) #wait 25ms for anything else to happen on the socket so we don't use 100% cpu on this one thread
    
    __idLock = multiprocessing.Lock()
    __currentSocketId = 0
    @staticmethod
    def __getSocketId():
        ret = None
        with WebSocketClient.__idLock:
            ret = WebSocketClient.__currentSocketId
            WebSocketClient.__currentSocketId = ret + 1
        return ret
    
    def __init__(self, wsManager, conn, addr):
        """Initializes the web socket client
        
        wsManager: websocket manager that can be used
        conn: socket object to use as the connection which has already had it's hand shaken
        addr: address of the client"""
        self.id = WebSocketClient.__getSocketId()
        self.serviceId = None #this is used externally to map this socket to a specific service
        self.wsManager = wsManager
        self.connection = conn
        self.address = addr
        self.open = True #we assume it is open
        self.sendQueue = Queue.Queue()
        self.recvQueue = Queue.Queue()
        self.lock = threading.Lock() #This lock only needs to be used when accessing anything but the queues
        self._readProgress = WebSocketClient.WebSocketRecvState()
        self._writeProgress = None
        wsManager.addWebSocket(self)
    
    def close(self):
        """Closes the connection"""
        if not self.open:
            return
        self.open = False
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()
