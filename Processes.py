"""Contains classes for dealing with process management for the WebSocketServer"""

import threading

class ProcessDirectory:
    """Class which is used to store processes in a "directory" tree. When a process
    is running, it can be found inside the process directories. When it isn't running
    the directory will not return the process and will remove it to signal the
    server to re-start the process. The ProcessDirectory class is safe for multithreading
    but not for multiprocessing."""
    
    class ProcessRecord:
        """Holds data for a process"""
        def __init__(self, process, sendQueue, recvQueue):
            self.process = process
            self.sendQueue = sendQueue
            self.recvQueue = recvQueue
        
        def is_alive(self):
            """Returns whether or not the associated process is still alive"""
            return self.process.is_alive()
    
    def __init__(self):
        """Creates a new process directory"""
        self._directoryLock = threading.Lock()
        self._directories = {} #additional directories after this one are stored here
        self._processes = {} #processes in this directory
    
    def getAllProcesses(self):
        """Returns a dictionary containing all the processes in this directory mapped to their
        process ids and the children processes"""
        ret = {}
        with self._directoryLock:
            for name in self._processes:
                #add our processes
                ret[self._processes[name].process.pid] = self._processes[name]
            for d in self._directories:
                #add our children's processes
                ret.update(self._directories[d].getAllProcesses())
        return ret
    
    def findDir(self, name):
        """Finds a child directory by name and returns it"""
        if name in self._directories:
            return self._directories[name]
        else:
            #we want to create a new empty one just in case they are using this as a way to create a directory
            self._directories[name] = ProcessDirectory()
            return self._directories[name]
    
    def findProcess(self, name):
        """Finds a named process. Returns None if no process is found under the
        given name"""
        ret = None
        with self._directoryLock:
            if name in self._processes:
                #check to make sure it is running
                if self._processes[name].is_alive():
                    ret = self._processes[name]
                else:
                    #it isn't running, so remove it from our list so it might be garbage collected later
                    self._processes.pop(name)
        return ret
    
    def addProcess(self, name, processRecord):
        """Adds the passed processes with the given name to the pool. If successful
        this returns true. Otherwise (as in the case of conflicting names or dead
        process) it returns false"""
        ret = True
        with self._directoryLock:
            if name in self._processes or processRecord.is_alive() == False:
                ret = False
            else:
                self._processes[name] = processRecord
        return ret
    
    def joinAll(self):
        """Waits for joining on all child threads. This is called recursivesly up
        the directory."""
        with self._directoryLock:
            for d in self._directories:
                #tell our children to join
                d.joinAll()
            for proc in self._processes:
                #wait for the process to join
                self._processes[proc].process.shutdownFlag.set()
                self._processes[proc].process.join()
