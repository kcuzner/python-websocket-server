"""Main file for the web socket server. This should be run to start the server
up"""

import socket
import base64
import hashlib
import Processes
import ConfigParser
import imp
import threading
import multiprocessing
import WebSockets
import sys
import getopt

HTTP_METHOD = "GET"
HTTP_VERSION = "HTTP/1.1"
HTTP_BAD_REQUEST = "400 Bad Request\r\n"
HTTP_NOT_FOUND = "404 Not Found\r\n"
HTTP_METHOD_NOT_ALLOWED = "405 Method Not Allowed\r\n" 
HTTP_SERVER_ERROR = "500 Internal Server Error\r\n"
HTTP_NOT_IMPLEMENTED = "501 Not Implemented\r\n"
WEBSOCKET_VERSION = "13"
WEBSOCKET_MAGIC_HANDSHAKE_STRING = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SERVICE_INDEX_NAME = "ws_service.py"

class WebSocketServer:
    """Encapsulates a websocketserver"""
    def __init__(self, overridePort=None, overrideHost=None, overrideDocRoot=None):
        self.directory = Processes.ProcessDirectory()
        self.config = None
        self.shutdownEvent = threading.Event()
        self.manager = multiprocessing.Manager()
        self.webSocketManager = WebSockets.WebSocketClient.WebSocketManager([], self.shutdownEvent, self.directory)
        self.webSocketManager.start()
        self.overridePort = overridePort
        self.overrideHost = overrideHost
        self.overrideDocRoot = overrideDocRoot
        #logging.basicConfig(filename="server.log", level=logging.DEBUG)
        
    def runServer(self):
        print "Loading configuration file..."
        self.config = ConfigParser.ConfigParser()
        r = self.config.read("server.config")
        if not r:
            print "ERROR: server.config not found"
            return
        
        HOST = self.overrideHost if self.overrideHost is not None else self.config.get('server', 'host')
        PORT = self.overridePort if self.overridePort is not None else self.config.getint('server', 'port')
        ADDR = (HOST, PORT)
        
        print "Attempting to start server on", ADDR
        
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
        server.bind(ADDR)
        server.listen(5)
        
        print "Server started. Listening for connections..."
        
        while self.shutdownEvent.is_set() == False:
            try:
                #get a new client
                conn, addr = server.accept()
                print "Client connected from", addr
                request = conn.recv(4096)
                response, close, serviceRecord = self.handshake(request)
                conn.send(response)
                if close:
                    print "Invalid request from", addr
                    conn.close()
                    continue
                client = WebSockets.WebSocketClient(self.webSocketManager, conn, addr)
                #link the client to the service record
                client.serviceId = serviceRecord.process.pid
                serviceRecord.recvQueue.put(WebSockets.WebSocketTransaction(WebSockets.WebSocketTransaction.TRANSACTION_NEWSOCKET, client.id, addr))
                
            except KeyboardInterrupt:
                self.shutdownEvent.set() #shut down gracefully
        
        print "Shutting down server..."
        self.directory.joinAll()
        
        server.shutdown(socket.SHUT_RDWR)
        server.close()
    
    def getService(self, location):
        """Attempts to load a service based on the location relative to the document root.
        location is the full path ->list<- including the index script if it was appended.
        Returns a Process.ProcessDirectory.ProcessRecord."""
        current = self.directory
        process = None
        for d in location:
            if d is location[-1]:
                #find the service
                process = current.findProcess(d)
                if process is None:
                    #attempt to load the service
                    incpath = "" #'.'.join(location)
                    path = (self.overrideDocRoot if self.overrideDocRoot is not None else self.config.get('server', 'document-root')) + '/'.join(location)
                    try:
                        service = imp.load_source(incpath, path)
                        sendQueue = self.manager.Queue()
                        recvQueue = self.manager.Queue()
                        s = service.Service(sendQueue, recvQueue)
                        s.start()
                        process = Processes.ProcessDirectory.ProcessRecord(s, sendQueue, recvQueue)
                        current.addProcess(location[-1], process)
                    except (ImportError, IOError):
                        #not found
                        pass
            else:
                #keep going down the directory
                current = current.findDir(d)
        return process
        
    
    def handshake(self, request):
        """Process a request header and creates a handshake for it"""
        service = None #service process to send this client to
        close = False #whether or not the connection should be closed
        response = HTTP_VERSION + " " #the http response to send to the client
        #process their initial HTTP request
        lines = request.split("\r\n")
        #the first line should contain their request
        heading = lines[0].split()
        if len(heading) != 3:
            close = True
            response += HTTP_BAD_REQUEST + "\r\n"
        elif heading[0] != HTTP_METHOD:
            #they did something other than a get request
            print heading[0]
            close = True
            response += HTTP_METHOD_NOT_ALLOWED + "\r\n"
        elif heading[2] != HTTP_VERSION:
            #they didn't say HTTP/1.1
            print heading[2]
            close = True
            response += HTTP_BAD_REQUEST + "\r\n"
        if close:
            #we are done here
            return response, close, service
        #find out if the service they want to contact exists
        location = heading[1].split('/')
        if location[0] == "":
            location.pop(0) #remove the empty string
        if location[-1] == "":
            #we need to append the "index" script to the location
            location[-1] = SERVICE_INDEX_NAME
        if location[-1][-3:] != ".py":
            location[-1] += ".py"
        service = self.getService(location)
        if service is None:
            #we are done
            close = True
            response += HTTP_NOT_FOUND + "\r\n"
            return response, close, service
        #now go through their headers
        headers = {}
        for line in lines:
            header = line.split(":")
            if len(header) is 1:
                #this is the http request header
                continue
            headers[header[0].strip()] = header[1].strip()
        if "Upgrade" not in headers or "Sec-WebSocket-Version" not in headers or "Sec-WebSocket-Key" not in headers:
            #they need to have certain headers
            close = True
            response += HTTP_BAD_REQUEST + "\r\n"
        elif headers["Upgrade"] != "websocket":
            #we need to ugprade to a web socket
            close = True
            response += HTTP_BAD_REQUEST + "\r\n"
        elif headers["Sec-WebSocket-Version"] != WEBSOCKET_VERSION:
            #this is built for version 13
            close = True
            response += HTTP_NOT_IMPLEMENTED + "\r\n"
        if close:
            #we are done here
            return response, close, service
        #process the web socket header and create the response
        response += "101 Switching Protocols\r\n"
        response += "Connection: Upgrade\r\n"
        response += "Upgrade: " + headers["Upgrade"] + "\r\n"
        #base64 decode the key
        response += "Sec-WebSocket-Accept: " + base64.encodestring(hashlib.sha1(headers["Sec-WebSocket-Key"] + WEBSOCKET_MAGIC_HANDSHAKE_STRING).digest()) + "\r\n"
        return response, close, service



def main():
    shortArgs = "p:h:d:"
    longArgs = [ "port=", "host=", "document-root="]
    showUsage = False
    overridePort = None
    overrideHost = None
    overrideDocRoot = None
    try:
        optlist, args = getopt.getopt(sys.argv[1:], shortArgs, longArgs)
        for opt in optlist:
            if opt[0] == "--port" or opt[0] == "-p":
                #override the port
                try:
                    overridePort = int(opt[1])
                except ValueError:
                    print "Invalid port number:", opt[1]
                    showUsage = True
            elif opt[0] == "--host" or opt[0] == "-h":
                #override host
                overrideHost = opt[1]
            elif opt[0] == "--document-root" or opt[0] == "-d":
                #override document root
                overrideDocRoot = opt[1]
    except getopt.GetoptError:
        #we get to print our usage message!
        showUsage = True
    if showUsage:
        print "Python WebSocket Server"
        print "Usage:"
        print "\t-p --port=\t\tOverride configured port number"
        print "\t-h --host=\t\tOverride configured host"
        print "\t-d --document-root=\tOverride configured document root"
        print "No arguments will start the server as configured in server.config"
        return
    
    server = WebSocketServer(overridePort, overrideHost, overrideDocRoot)
    server.runServer()

if __name__ == "__main__":
    main()
