"""Contains classes for dealing with process management for the WebSocketServer"""

import time
import multiprocessing

class DelegatingProcessPool:
    """Pool of processes which are used for executing delegation functions"""
    def __init__(self):
        self.numWorkers = multiprocessing.cpu_count()
        self.stopSignal = multiprocessing.Event() #this is set when any of the child threads gets a keyboard interrupt or when the processes need to stop
        self.jobQueue = multiprocessing.Queue()
        self.workers = []
        #start a maintenance thread to ensure we always have the right amount of workers
        maintenance = multiprocessing.Process(None, self.maintenance)
        maintenance.start()
    
    def maintenance(self):
        """Runs in the background to ensure the correct number of processes are running"""
        try:
            while self.stopSignal.is_set() == False:
                if len(self.workers) < self.numWorkers:
                    #one stopped somehow
                    worker = multiprocessing.Process(None, self.worker)
                    worker.start()
                    self.workers.append(worker)
                else:
                    time.sleep(0.1) #wait 1/10 second to do this again
        except KeyboardInterrupt:
            self.stopSignal.set()
    
    def worker(self):
        """Works on a process"""
        try:
            while self.stopSignal.is_set() == False:
                if self.jobQueue.empty():
                    time.sleep(0.010) #wait 10ms for the next job
                    continue
                toRun = self.jobQueue.get()
                toRun()
        except KeyboardInterrupt:
            #take down all our processes
            self.stopSignal.set()

class ProcessDirectory:
    """Class which is used to store processes in a "directory" tree. When a process
    is running, it can be found inside the process directories. When it isn't running
    the directory will not return the process and will remove it to signal the
    server to re-start the process"""
    def __init__(self):
        """Creates a new process directory"""
        self._directories = {} #additional directories after this one are stored here
        self._processes = {} #processes in this directory
    
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
        if name in self._processes:
            #check to make sure it is running
            if self._processes[name].is_alive():
                return self._processes[name]
            else:
                #it isn't running, so remove it from our list so it might be garbage collected later
                self._processes.pop(name)
                return None
        else:
            return None
    
    def addProcess(self, name, process):
        """Adds the passed processes with the given name to the pool. If successful
        this returns true. Otherwise (as in the case of conflicting names or dead
        process) it returns false"""
        if name in self._processes or process.is_alive() == False:
            return False
        else:
            self._processes[name] = process
            return True
    
    def joinAll(self):
        """Waits for joining on all child threads. This is called recursivesly up
        the directory."""
        for d in self._directories:
            #tell our children to join
            d.joinAll()
        for proc in self._processes:
            #wait for the process to join
            self._processes[proc].shutdownFlag.set()
            self._processes[proc].join()
