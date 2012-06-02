
import socket
import select
import struct
import threading
import Queue
import time
import collections
import sys

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

class WebSocketClient:
    """Class which takes care of sending and receiving data to and from a HTTP WebSocket."""
    
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
                print self.state
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
    
    
    
    class WebSocketSendRecvThread(threading.Thread):
        """Thread which takes care of sending and receiving asyncronously from a WebSocket.
        
        Each WebSocket must have a Queue.Queue called sendQueue. String messages to be sent should
        be placed into that queue.
        
        There must be a Queue.Queue called recvQueue as well. When a receive is completed, the received
        string will be placed in this queue and handle_recv will be called. Note that handle_recv is called
        after all packets have been received for the select loop and so it could possibly contain multiple
        received strings. Progress on the current item will be stored in a variable in the WebSocketClient
        class called _writeProgress.
        
        The WebSocketSendRecvThread will store in progress reads in a variable in the WebSocketClient
        class called _readProgress.
        
        Note that since this is asncyronous send and receive times are not guaranteed and a sent message
        could be sent and received before a message sent from other other end could be entirely read."""
        
        def __init__(self, socketList):
            """Initializes a new WebSocketSendRecvThread"""
            threading.Thread.__init__(self)
            self.sockets = socketList
            self.socketListLock = threading.Lock()
        
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
        
        def run(self):
            """Main thread method which will run until all sockets are no longer active"""
            with self.socketListLock:
                l = len(self.sockets)
            while l > 0 and WebSocketClient._sendRecvStopEvent.is_set() == False:
                with self.socketListLock:
                    for s in self.sockets:
                        if s.open == False:
                            #remove this socket from our list
                            self.sockets.remove(s)
                        #sadly, we need to call select on every socket individually so that we can keep track of the WebSocketClient class
                        sList = [ s.connection ]
                        try:
                            r, w, x = select.select(sList, sList, sList, 0)
                        except socket.error:
                            #it failed. remove this client
                            s.open = False #this is no longer open, but we can't call close since it would break it some more
                        if r:
                            #the socket is ready to be read
                            try:
                                received = r[0].recv(4096) #we will receive up to 4096 bytes
                                receivedBytes = bytearray(received)
                                while len(receivedBytes) > 0:
                                    receivedBytes = s._readProgress.receive(receivedBytes)
                                    if s._readProgress.state == WebSocketClient.WebSocketRecvState.STATE_DONE:
                                        #a string was read, so put it in the queue
                                        try:
                                            s.recvQueue.put_nowait(s._readProgress.unmaskedPayloadBytes.decode(sys.getdefaultencoding()))
                                        except Queue.Full:
                                            print "Notice: Receive queue full on WebSocketClient", s, "... did you forget to empty the queue or call task_done?"
                                            pass #oh well...I guess their data gets to be lost since they didn't bother to empty their queue
                                        s._readProgress = WebSocketClient.WebSocketRecvState() #reset the progress
                            except socket.error:
                                pass #don't worry about it
                        if w:
                            #the socket is ready to be written
                            if s._writeProgress != None:
                                #we still have something to write
                                s._writeProgress = self._sendToSocket(s._writeProgress, s.connection)
                            elif not s.sendQueue.empty():
                                #there is something new to start sending
                                try:
                                    toWrite = self._stringToFrame(s.sendQueue.get_nowait())
                                    self._sendToSocket(toWrite, s.connection)
                                    s.sendQueue.task_done()
                                except Queue.Empty:
                                    pass #don't worry about it...we just couldn't get anything
                        #find out if their received event needs to be called
                        if s.recvQueue.empty() != True and s.handle_recv != None:
                            s.handle_recv()
                    l = len(self.sockets)
                time.sleep(0.025) #wait 25ms for anything else to happen on the socket
    
    _sendRecvThread = WebSocketSendRecvThread( [] )
    _sendRecvThread.start() #it will immediately stop...this is for initial state
    _sendRecvStopEvent = threading.Event() #this should be set by the parent thread/process to shut down all reads and writes
    @staticmethod
    def _addWebSocket(s):
        """Adds a socket to the list to be asyncronously managed"""
        if WebSocketClient._sendRecvThread.isAlive() == False:
            #create a new one
            WebSocketClient._sendRecvThread = WebSocketClient.WebSocketSendRecvThread([ s ])
            WebSocketClient._sendRecvThread.start()
        else:
            #add to the existing one
            with WebSocketClient._sendRecvThread.socketListLock:
                WebSocketClient._sendRecvThread.sockets.append(s)
    
    def __init__(self, conn, addr):
        """Initializes the web socket
        
        conn: socket object to use as the connection which has already had it's hand shaken
        addr: address of the client
        
        Note that the variable handle_recv must be set after this initialization otherwise
        eventually the receive queue will overflow and data will be lost."""
        self.connection = conn
        self.address = addr
        self.open = True #we assume it is open
        self.handle_recv = None
        self.sendQueue = Queue.Queue()
        self.recvQueue = Queue.Queue()
        self._readProgress = WebSocketClient.WebSocketRecvState()
        self._writeProgress = None
        WebSocketClient._addWebSocket(self)
    
    def send(self, data):
        """Queues some data for sending over the connection for this object. Data should be a string"""
        self.sendQueue.put(data)
    
    def close(self):
        """Closes the connection"""
        self.open = False
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()
