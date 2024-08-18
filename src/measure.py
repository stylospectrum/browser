import time
import threading


class MeasureTime:
    def __init__(self):
        self.lock = threading.Lock()
        self.file = open("browser.trace", "w")
        self.file.write('{"traceEvents": [')
        ts = time.time() * 1000000
        self.file.write(
            '{ "name": "process_name",' +
            '"ph": "M",' +
            '"ts": ' + str(ts) + ',' +
            '"pid": 1, "cat": "__metadata",' +
            '"args": {"name": "Browser"}}')
        self.file.flush()

    def time(self, name):
        ts = time.time() * 1000000
        tid = threading.get_ident()
        self.lock.acquire(blocking=True)
        self.file.write(
            ', { "ph": "B", "cat": "_",' +
            '"name": "' + name + '",' +
            '"ts": ' + str(ts) + ',' +
            '"pid": 1, "tid": ' + str(tid) + '}')
        self.file.flush()
        self.lock.release()

    def stop(self, name):
        ts = time.time() * 1000000
        tid = threading.get_ident()
        self.lock.acquire(blocking=True)
        self.file.write(
            ', { "ph": "E", "cat": "_",' +
            '"name": "' + name + '",' +
            '"ts": ' + str(ts) + ',' +
            '"pid": 1, "tid": ' + str(tid) + '}')
        self.file.flush()
        self.lock.release()

    def finish(self):
        self.lock.acquire(blocking=True)
        for thread in threading.enumerate():
            self.file.write(
                ', { "ph": "M", "name": "thread_name",' +
                '"pid": 1, "tid": ' + str(thread.ident) + ',' +
                '"args": { "name": "' + thread.name + '"}}')
        self.file.write(']}')
        self.file.close()
        self.lock.release()
