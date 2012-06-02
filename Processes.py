"""Contains classes for dealing with process management for the WebSocketServer"""

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
