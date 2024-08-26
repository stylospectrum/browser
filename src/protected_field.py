class ProtectedField:
    def __init__(self) -> None:
        self.value = None
        self.dirty = True
        self.invalidations: set['ProtectedField'] = set()

    def mark(self):
        if self.dirty: return
        self.dirty = True

    def get(self):
        assert not self.dirty
        return self.value
    
    def set(self, value):
        self.notify()
        self.value = value
        self.dirty = False

    def notify(self):
        for field in self.invalidations:
            field.mark()

    def read(self, notify: 'ProtectedField'):
        self.invalidations.add(notify)
        return self.get()
    
    def copy(self, field: 'ProtectedField'):
        self.set(field.read(notify=self))
    
    def __str__(self):
        if self.dirty:
            return "<dirty>"
        else:
            return str(self.value)

    def __repr__(self):
        return "ProtectedField({}, {})".format(
            self.obj.node if hasattr(self.obj, "node") else self.obj,
            self.name)