import threading


class Task:
    def __init__(self, task_code, *args):
        self.task_code = task_code
        self.args = args

    def run(self):
        self.task_code(*self.args)
        self.task_code = None
        self.args = None


class TaskRunner:
    def __init__(self, tab):
        self.tab = tab
        self.tasks: list[Task] = []
        self.needs_quit = False
        self.condition = threading.Condition()
        self.main_thread = threading.Thread(
            target=self.run,
            name="Main thread",
        )

    def start_thread(self):
        self.main_thread.start()

    def set_needs_quit(self):
        self.condition.acquire(blocking=True)
        self.needs_quit = True
        self.condition.notify_all()
        self.condition.release()

    def schedule_task(self, task: Task):
        self.condition.acquire(blocking=True)
        self.tasks.append(task)
        self.condition.notify_all()
        self.condition.release()

    def clear_pending_tasks(self):
        self.condition.acquire(blocking=True)
        self.tasks.clear()
        self.pending_scroll = None
        self.condition.release()

    def handle_quit(self):
        pass

    def run(self):
        while True:
            self.condition.acquire(blocking=True)
            needs_quit = self.needs_quit
            self.condition.release()
            if needs_quit:
                self.handle_quit()
                return

            task = None
            self.condition.acquire(blocking=True)
            if len(self.tasks) > 0:
                task = self.tasks.pop(0)
            self.condition.release()
            if task:
                task.run()

            self.condition.acquire(blocking=True)
            if len(self.tasks) == 0 and not self.needs_quit:
                self.condition.wait()
            self.condition.release()
